"""VigilIntel STIX Importer - Core connector logic."""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
import yaml
from pycti import OpenCTIConnectorHelper, get_config_variable


class VigilIntelConnector:
    """OpenCTI external-import connector for VigilIntel STIX reports.

    Downloads daily STIX 2.x threat intelligence reports from the
    VigilIntel GitHub repository and imports them into OpenCTI.
    Supports backfill (lookback) and deduplication via connector state.
    """

    # Base URL template for raw GitHub content
    _BASE_URL = (
        "https://raw.githubusercontent.com/kidrek/VigilIntel/main"
        "/{year}/{month}/{year}-{month}-{day}-report.stix_{lang}.json"
    )

    def __init__(self):
        """Initialize the connector, read configuration, and set up helper."""

        # ── Load YAML config (if present) ──────────────────────────────
        config_file_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.yml"
        )
        config = {}
        if os.path.isfile(config_file_path):
            with open(config_file_path, "r") as f:
                config = yaml.load(f, Loader=yaml.SafeLoader) or {}

        # ── OpenCTI connector helper ──────────────────────────────────
        self.helper = OpenCTIConnectorHelper(config)

        # ── Connector-specific settings ────────────────────────────────
        self.language = get_config_variable(
            "VIGILINTEL_LANGUAGE",
            ["vigilintel", "language"],
            config,
            default="fr",
        )
        if self.language not in ("fr", "en"):
            self.helper.connector_logger.warning(
                "Invalid VIGILINTEL_LANGUAGE '%s', defaulting to 'fr'.",
                self.language,
            )
            self.language = "fr"

        self.lookback_days = int(
            get_config_variable(
                "VIGILINTEL_LOOKBACK_DAYS",
                ["vigilintel", "lookback_days"],
                config,
                default="7",
            )
        )

        self.interval_hours = int(
            get_config_variable(
                "VIGILINTEL_INTERVAL_HOURS",
                ["vigilintel", "interval_hours"],
                config,
                default="24",
            )
        )

        self.base_url = get_config_variable(
            "VIGILINTEL_BASE_URL",
            ["vigilintel", "base_url"],
            config,
            default=self._BASE_URL,
        )

        self.helper.connector_logger.info(
            "[VigilIntel] Connector initialised — language=%s, lookback=%d days, interval=%dh",
            self.language,
            self.lookback_days,
            self.interval_hours,
        )

    # ─── URL helpers ──────────────────────────────────────────────────

    def _build_url(self, target_date: datetime) -> str:
        """Build the raw GitHub URL for a given date and configured language."""
        return self.base_url.format(
            year=target_date.strftime("%Y"),
            month=target_date.strftime("%m"),
            day=target_date.strftime("%d"),
            lang=self.language,
        )

    # ─── Date range helpers ───────────────────────────────────────────

    def _compute_date_range(self) -> list[datetime]:
        """Determine which dates to process.

        If the connector has never run (no persisted state), we look back
        ``lookback_days`` into the past.  Otherwise we only fetch from the
        day after the last successfully processed date up to today.

        Returns a list of ``datetime`` objects in chronological order.
        """
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Read persisted state
        state = self.helper.get_state()
        last_processed = None
        if state and "last_processed_date" in state:
            try:
                last_processed = datetime.fromisoformat(
                    state["last_processed_date"]
                ).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                last_processed = None

        if last_processed is None:
            # First run → backfill
            start_date = today - timedelta(days=self.lookback_days)
            self.helper.connector_logger.info(
                "[VigilIntel] First run detected — backfilling from %s to %s",
                start_date.strftime("%Y-%m-%d"),
                today.strftime("%Y-%m-%d"),
            )
        else:
            start_date = last_processed + timedelta(days=1)
            if start_date > today:
                self.helper.connector_logger.info(
                    "[VigilIntel] Already up-to-date (last processed: %s).",
                    last_processed.strftime("%Y-%m-%d"),
                )
                return []

        dates = []
        current = start_date
        while current <= today:
            dates.append(current)
            current += timedelta(days=1)

        return dates

    # ─── Download & validate ──────────────────────────────────────────

    def _download_report(self, url: str) -> dict | None:
        """Download a STIX bundle from the given URL.

        Returns the parsed JSON dict, or ``None`` on failure.
        """
        try:
            self.helper.connector_logger.info(
                "[VigilIntel] Fetching %s", url
            )
            response = requests.get(url, timeout=30)

            if response.status_code == 404:
                self.helper.connector_logger.warning(
                    "[VigilIntel] Report not found (404): %s", url
                )
                return None

            response.raise_for_status()
            return response.json()

        except requests.exceptions.JSONDecodeError:
            self.helper.connector_logger.error(
                "[VigilIntel] Invalid JSON received from %s", url
            )
            return None
        except requests.exceptions.RequestException as e:
            self.helper.connector_logger.error(
                "[VigilIntel] Network error fetching %s: %s", url, str(e)
            )
            return None

    @staticmethod
    def _validate_stix_bundle(bundle: dict) -> bool:
        """Perform minimal validation on a STIX 2.x bundle."""
        if not isinstance(bundle, dict):
            return False
        if bundle.get("type") != "bundle":
            return False
        if "objects" not in bundle or not isinstance(bundle["objects"], list):
            return False
        return True

    # ─── Ingestion ────────────────────────────────────────────────────

    def _send_to_opencti(self, bundle: dict, work_id: str) -> bool:
        """Serialize the STIX bundle and send it to OpenCTI.

        Returns True on success, False on failure.
        """
        try:
            serialized = json.dumps(bundle)
            self.helper.send_stix2_bundle(
                serialized,
                update=True,
                work_id=work_id,
            )
            return True
        except Exception as e:
            self.helper.connector_logger.error(
                "[VigilIntel] Failed to send bundle to OpenCTI: %s", str(e)
            )
            return False

    # ─── Main processing loop ─────────────────────────────────────────

    def _process_dates(self) -> None:
        """Core logic: compute dates, fetch, validate, ingest."""
        dates = self._compute_date_range()
        if not dates:
            return

        total = len(dates)
        success_count = 0
        skip_count = 0
        error_count = 0
        last_success_date = None

        friendly_name = (
            f"VigilIntel run @ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        work_id = self.helper.api.work.initiate_work(
            self.helper.connect_id, friendly_name
        )

        self.helper.connector_logger.info(
            "[VigilIntel] Processing %d date(s)…", total
        )

        for idx, target_date in enumerate(dates, start=1):
            date_str = target_date.strftime("%Y-%m-%d")
            self.helper.connector_logger.info(
                "[VigilIntel] [%d/%d] Processing %s…", idx, total, date_str
            )

            url = self._build_url(target_date)
            bundle = self._download_report(url)

            if bundle is None:
                skip_count += 1
                # Still mark date as processed so we don't retry missing dates forever
                last_success_date = target_date
                continue

            if not self._validate_stix_bundle(bundle):
                self.helper.connector_logger.error(
                    "[VigilIntel] Invalid STIX bundle for %s — skipping.",
                    date_str,
                )
                error_count += 1
                last_success_date = target_date
                continue

            nb_objects = len(bundle.get("objects", []))
            self.helper.connector_logger.info(
                "[VigilIntel] Valid STIX bundle for %s — %d objects.",
                date_str,
                nb_objects,
            )

            if self._send_to_opencti(bundle, work_id):
                success_count += 1
                last_success_date = target_date
            else:
                error_count += 1

        # ── Update connector state ────────────────────────────────────
        if last_success_date is not None and success_count >= 1:
            new_state = {
                "last_processed_date": last_success_date.isoformat(),
                "last_run": datetime.now(timezone.utc).isoformat(),
            }
            self.helper.set_state(new_state)
            self.helper.connector_logger.info(
                "[VigilIntel] State updated — last_processed_date=%s",
                last_success_date.strftime("%Y-%m-%d"),
            )

        # ── Finalize work ─────────────────────────────────────────────
        message = (
            f"VigilIntel run complete — "
            f"{success_count} imported, {skip_count} skipped, {error_count} errors "
            f"(out of {total} dates)"
        )
        self.helper.connector_logger.info("[VigilIntel] %s", message)
        self.helper.api.work.to_processed(work_id, message)

    # ─── Scheduler entry-point ────────────────────────────────────────

    def run(self) -> None:
        """Main loop — runs processing then sleeps for the configured interval."""
        self.helper.connector_logger.info(
            "[VigilIntel] Connector started."
        )

        while True:
            try:
                self._process_dates()
            except Exception as e:
                self.helper.connector_logger.error(
                    "[VigilIntel] Unexpected error during processing: %s",
                    str(e),
                )

            # Sleep until next run
            sleep_seconds = self.interval_hours * 3600
            self.helper.connector_logger.info(
                "[VigilIntel] Sleeping %d hours until next run…",
                self.interval_hours,
            )
            time.sleep(sleep_seconds)

import logging
import time

from rq.worker import Worker
from sqlalchemy.exc import OperationalError

from .core.config import get_settings
from .core.logging_config import configure_logging
from .core.database import Base, get_engine
from .queue_factory import get_queue
from .services.onboarding import OnboardingService

LOGGER = logging.getLogger(__name__)
WAIT_SECONDS = 5
SETTINGS = get_settings()
configure_logging(SETTINGS)


def _wait_for_onboarding() -> None:
    while True:
        try:
            service = OnboardingService()
            if not service.settings_service.needs_onboarding():
                LOGGER.info("Onboarding complete; starting worker queue")
                return
            LOGGER.info(
                "Worker waiting for onboarding to finish before starting (retry in %s s)",
                WAIT_SECONDS,
            )
        except OperationalError:
            LOGGER.info("Database not ready yet; worker retrying in %s s", WAIT_SECONDS)
        time.sleep(WAIT_SECONDS)


def main() -> None:
    _wait_for_onboarding()
    queue = get_queue()
    worker = Worker([queue], connection=queue.connection)
    worker.work(
        with_scheduler=True,
        logging_level=(SETTINGS.log_level or "INFO").upper(),
    )


if __name__ == "__main__":
    main()

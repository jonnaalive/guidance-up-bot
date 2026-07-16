from __future__ import annotations

import argparse
import logging
import time

from app.config import Settings
from app.discord_client import DiscordNotifier
from app.service import GuidanceService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SEC 가이던스 상향 Discord 알림")
    parser.add_argument("--once", action="store_true", help="한 번만 스캔하고 종료")
    parser.add_argument("--dry-run", action="store_true", help="Discord 전송 없이 스캔")
    parser.add_argument("--test-discord", action="store_true", help="Discord 연결 테스트")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env(dry_run=args.dry_run)
    if args.test_discord:
        if not settings.discord_webhook_url:
            raise SystemExit("DISCORD_WEBHOOK_URL이 필요합니다.")
        DiscordNotifier(settings.discord_webhook_url).send_test()
        print("Discord 테스트 메시지를 보냈습니다.")
        return
    settings.validate(require_discord=not args.dry_run)
    service = GuidanceService(settings)
    while True:
        stats = service.run_once()
        logging.info("스캔 완료: %s", stats)
        if args.once:
            return
        time.sleep(settings.poll_minutes * 60)


if __name__ == "__main__":
    main()

"""
Pipeline orchestrator — run all medallion layers sequentially.
Usage: python pipeline.py [--bronze] [--silver] [--gold] [--alerts]
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(__file__))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def run_bronze():
    from apps.bronze.ingest import ingest_all
    ingest_all()


def run_silver():
    from apps.silver.transform import transform_all
    transform_all()


def run_gold(limit=None):
    from apps.gold.star_schema import build_star_schema
    build_star_schema(limit=limit)


def run_critical():
    from apps.gold.critical_tickets import detect_critical
    detect_critical()


def run_alerts():
    from apps.gold.send_alerts import send_alerts
    send_alerts()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GLPI Light Pipeline")
    parser.add_argument("--bronze", action="store_true", help="Run bronze ingestion")
    parser.add_argument("--silver", action="store_true", help="Run silver transformation")
    parser.add_argument("--gold", action="store_true", help="Run gold star schema")
    parser.add_argument("--gold-limit", type=int, default=None, help="Limit gold to N tickets (test)")
    parser.add_argument("--critical", action="store_true", help="Run critical ticket detection")
    parser.add_argument("--alerts", action="store_true", help="Send email alerts")
    parser.add_argument("--all", action="store_true", help="Run full pipeline")

    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(1)

    if args.all or args.bronze:
        print("\n" + "=" * 50)
        print("BRONZE LAYER - MySQL -> PostgreSQL")
        print("=" * 50)
        run_bronze()

    if args.all or args.silver:
        print("\n" + "=" * 50)
        print("SILVER LAYER - Bronze -> Silver (cleaning + features)")
        print("=" * 50)
        run_silver()

    if args.all or args.gold:
        print("\n" + "=" * 50)
        print("GOLD LAYER - Star Schema (dimensions + facts)")
        print("=" * 50)
        run_gold(limit=args.gold_limit)

    if args.all or args.critical:
        print("\n" + "=" * 50)
        print("CRITICAL TICKETS - Detection + gold_alert")
        print("=" * 50)
        run_critical()

    if args.all or args.alerts:
        print("\n" + "=" * 50)
        print("EMAIL ALERTS - Send critical ticket notifications")
        print("=" * 50)
        run_alerts()

    print("\nPipeline complete.")

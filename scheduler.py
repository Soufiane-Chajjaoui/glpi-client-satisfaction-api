"""
Planificateur APScheduler pour le pipeline GLPI Light.
Usage : python scheduler.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from apscheduler.schedulers.blocking import BlockingScheduler
from pipeline import run_bronze, run_silver, run_gold, run_critical, run_alerts


def pipeline_quotidien():
    print("\n" + "=" * 60)
    print("EXECUTION PIPELINE QUOTIDIEN")
    print("=" * 60)
    run_bronze()
    run_silver()
    run_gold()
    run_critical()
    run_alerts()
    print("Pipeline termine.\n")


if __name__ == "__main__":
    scheduler = BlockingScheduler()

    # Tous les jours a 6h00
    scheduler.add_job(pipeline_quotidien, "cron", hour=6, minute=0, id="pipeline_daily")

    print("Planificateur demarre. Pipeline quotidien a 6h00.")
    print("Declenchement unique a 17:30 pour test.")
    print("Appuyez sur Ctrl+C pour arreter.")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\nPlanificateur arrete.")

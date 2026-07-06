"""
Silver quality checks — row counts, nulls, duplicates, value ranges, date sanity.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from lib.db import pg_engine

TABLES = [
    "glpi_tickets", "glpi_itilfollowups", "glpi_tickettasks",
    "glpi_itilsolutions", "glpi_ticketsatisfactions", "glpi_entities",
    "glpi_users", "glpi_itilcategories", "glpi_groups_tickets",
    "glpi_locations", "glpi_profiles", "glpi_profiles_users",
    "glpi_slalevelactions", "glpi_slalevels",
    "glpi_slas", "glpi_tickets_users",
]

# Critical columns that must NOT be null per table
NOT_NULL = {
    "glpi_tickets":               ["id", "entities_id", "date_creation", "status", "priority", "name", "content"],
    "glpi_itilfollowups":         ["id", "items_id", "itemtype", "date", "content"],
    "glpi_tickettasks":           ["id", "tickets_id", "date", "content"],
    "glpi_itilsolutions":         ["id", "items_id", "itemtype", "date_creation", "content"],
    "glpi_ticketsatisfactions":   ["id", "tickets_id"],
    "glpi_entities":              ["id", "name", "entities_id"],
    "glpi_users":                 ["id", "name"],
    "glpi_itilcategories":        ["id", "name", "entities_id"],
    "glpi_groups_tickets":        ["id", "groups_id", "tickets_id"],
    "glpi_locations":             ["id", "name"],
    "glpi_profiles":              ["id", "name"],
    "glpi_profiles_users":        ["id", "users_id", "profiles_id"],
    "glpi_slalevelactions":       ["id"],
    "glpi_slalevels":             ["id"],
    "glpi_slas":                  ["id", "name", "type"],
    "glpi_tickets_users":         ["id", "tickets_id", "users_id", "type"],
}

PRIMARY_KEYS = {
    "glpi_tickets":               "id",
    "glpi_itilfollowups":         "id",
    "glpi_tickettasks":           "id",
    "glpi_itilsolutions":         "id",
    "glpi_ticketsatisfactions":   "id",
    "glpi_entities":              "id",
    "glpi_users":                 "id",
    "glpi_itilcategories":        "id",
    "glpi_groups_tickets":        "id",
    "glpi_locations":             "id",
    "glpi_profiles":              "id",
    "glpi_profiles_users":        "id",
    "glpi_slalevelactions":       "id",
    "glpi_slalevels":             "id",
    "glpi_slas":                  "id",
    "glpi_tickets_users":         "id",
}

VALUE_RANGES = {
    "glpi_tickets":               [("priority", 1, 5), ("status", 1, 6), ("is_deleted", 0, 1)],
    "glpi_ticketsatisfactions":   [("type", 1, 1)],
    "glpi_tickets_users":         [("type", 1, 4)],
    "glpi_slas":                  [("type", 0, 1)],
    "glpi_groups_tickets":        [("type", 1, 3)],
}


def run_q(conn, sql, params=None):
    """Run a single query; rollback on error so subsequent queries work."""
    try:
        if params:
            return conn.execute(text(sql), params).scalar() or 0
        return conn.execute(text(sql)).scalar() or 0
    except Exception as e:
        conn.rollback()
        return None


def check_row_counts(conn):
    ok = True
    print(f"\n{'Table':30} {'Bronze':>8} {'Silver':>8} {'Diff':>8}")
    print("-" * 60)
    for t in TABLES:
        b = run_q(conn, f"SELECT COUNT(*) FROM bronze.{t}")
        s = run_q(conn, f"SELECT COUNT(*) FROM silver.{t}")
        if b is None or s is None:
            print(f"{t:30} {'ERR':>8} {'ERR':>8} {'ERR':>8}")
            ok = False
        else:
            diff = s - b
            print(f"{t:30} {b:>8} {s:>8} {diff:>8}")
            if diff != 0:
                ok = False
    return ok


def check_nulls(conn):
    ok = True
    print(f"\n{'Table':30} {'Column':25} {'Nulls':>8}")
    print("-" * 68)
    for t, cols in NOT_NULL.items():
        for col in cols:
            n = run_q(conn, f"SELECT COUNT(*) FROM silver.{t} WHERE {col} IS NULL")
            if n is None:
                print(f"{t:30} {col:25} {'ERR':>8}")
                ok = False
            elif n > 0:
                print(f"{t:30} {col:25} {str(n):>8}")
                ok = False
    if ok:
        print(f"{'Aucun null critique':30}")
    return ok


def check_duplicates(conn):
    ok = True
    print(f"\n{'Table':30} {'Doublons PK':>12}")
    print("-" * 44)
    for t, pk in PRIMARY_KEYS.items():
        n = run_q(conn, f"SELECT COUNT(*) - COUNT(DISTINCT {pk}) FROM silver.{t}")
        if n is None:
            print(f"{t:30} {'ERR':>12}")
            ok = False
        elif n > 0:
            print(f"{t:30} {str(n):>12}")
            ok = False
    if ok:
        print(f"{'Aucun doublon':30}")
    return ok


def check_value_ranges(conn):
    ok = True
    print(f"\n{'Table':30} {'Column':20} {'Hors-bornes':>12}")
    print("-" * 64)
    for t, checks in VALUE_RANGES.items():
        for col, lo, hi in checks:
            n = run_q(conn, f"SELECT COUNT(*) FROM silver.{t} WHERE {col} IS NOT NULL AND ({col} < {lo} OR {col} > {hi})")
            if n is None:
                print(f"{t:30} {col:20} {'ERR':>12}")
                ok = False
            elif n > 0:
                print(f"{t:30} {col:20} {str(n):>12}")
                ok = False
    if ok:
        print(f"{'Tout dans les bornes':30}")
    return ok


def check_date_sanity(conn):
    ok = True
    print(f"\n{'Table':30} {'Futur':>8} {'Avant 2000':>12}")
    print("-" * 52)
    date_cols = {
        "glpi_tickets": ["date_creation", "date_mod"],
        "glpi_itilfollowups": ["date"],
        "glpi_itilsolutions": ["date_creation"],
    }
    for t, cols in date_cols.items():
        for col in cols:
            fut = run_q(conn, f"SELECT COUNT(*) FROM silver.{t} WHERE ({col})::timestamp > NOW() + INTERVAL '1 day'")
            old = run_q(conn, f"SELECT COUNT(*) FROM silver.{t} WHERE ({col})::timestamp < '2000-01-01' AND {col} IS NOT NULL")
            if fut is None or old is None:
                print(f"{t:30} {'ERR':>8} {'ERR':>12}")
            elif fut > 0 or old > 0:
                print(f"{t:30} {str(fut):>8} {str(old):>12}")
                ok = False
    if ok:
        print(f"{'Dates saines':30}")
    return ok


HTML_TABLES = {
    "glpi_tickets": "content",
    "glpi_itilfollowups": "content",
    "glpi_itilsolutions": "content",
}


def check_html_in_content(conn):
    ok = True
    print(f"\n{'Table':25} {'Total':>8} {'Avec HTML':>10} {'%':>7}")
    print("-" * 53)
    for table, col in HTML_TABLES.items():
        total = run_q(conn, f"SELECT COUNT(*) FROM silver.{table}")
        has = run_q(conn, f"SELECT COUNT(*) FROM silver.{table} WHERE {col} ~ '<[A-Za-z/][^>]*>'")
        if total is None or has is None:
            print(f"{table:25} {'ERR':>8} {'ERR':>10}")
            ok = False
        else:
            pct = round(has / total * 100, 1) if total else 0
            print(f"{table:25} {total:>8} {has:>10} {pct:>6}%")
            if has > 0:
                ok = False
    return ok


def check_all():
    print("=" * 60)
    print("QUALITE SILVER — verification complete")
    print("=" * 60)
    with pg_engine().connect() as conn:
        r1 = check_row_counts(conn)
        r2 = check_nulls(conn)
        r3 = check_duplicates(conn)
        r4 = check_value_ranges(conn)
        r5 = check_date_sanity(conn)
        r6 = check_html_in_content(conn)

    hard_ok = all([r1, r3])
    soft_ok = all([r2, r4, r5, r6])
    print("\n" + "=" * 60)
    if hard_ok and soft_ok:
        print("RESULTAT : TOUT OK — les donnees Silver sont propres")
    elif hard_ok:
        print("RESULTAT : OK (avertissements) — petits ecarts non bloquants")
    else:
        print("RESULTAT : PROBLEMES BLOQUANTS — voir ci-dessus")
    print("=" * 60)


if __name__ == "__main__":
    check_all()

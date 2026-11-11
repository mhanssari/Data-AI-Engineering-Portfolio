"""Dimensions after each batch: late reference data, store history, corrected cohorts and partial periods."""

from conftest import query


def test_a_product_sold_before_master_data_is_inferred_then_completed(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = "select product_name, category, is_inferred from marts.dim_product where product_id = 'P061'"
    assert query(after_1, sql) == [("Unknown product P061", "Unknown", True)]  # D10
    assert query(after_2, sql) == [("Seasonal gift set", "Gifts", False)]
    assert query(after_2, "select count(*) from marts.dim_product where is_inferred") == [(0,)]


def test_a_store_version_takes_effect_on_its_date(after_batches) -> None:
    _, warehouse = after_batches
    sql = """
        select cast(d.date_day as varchar), s.store_format
        from marts.dim_date d
        join marts.dim_store s
            on s.store_id = 'S003' and d.date_day >= s.valid_from_date and d.date_day < s.valid_to_date
        where d.date_day in ('2025-08-31', '2025-09-01')
        order by 1
    """
    assert query(warehouse, sql) == [("2025-08-31", "high_street"), ("2025-09-01", "retail_park")]  # D9


def test_a_version_announced_in_batch_2_is_absent_after_batch_1(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = "select count(*) from marts.dim_store where store_id = 'S005'"
    assert query(after_1, sql) == [(1,)] and query(after_2, sql) == [(2,)]


def test_a_corrected_enrolment_moves_the_member_cohort(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = (
        "select cast(enrolment_month_start_date as varchar) from marts.dim_member where member_id = 'M90004'"
    )
    assert query(after_1, sql) == [("2025-03-01",)] and query(after_2, sql) == [("2025-02-01",)]  # D12


def test_the_as_of_date_marks_partial_months(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = """
        select max(case when not is_after_as_of_date then cast(date_day as varchar) end)
            , bool_and(is_complete_month) filter (where month_start_date = '2026-09-01')
        from marts.dim_date
    """
    assert query(after_1, sql) == [("2026-09-16", False)]
    assert query(after_2, sql) == [("2026-09-30", True)]

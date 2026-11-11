"""Member-month and cohort marts: members who never bought, corrected cohorts, and immature cohorts."""

from conftest import query


def test_a_member_who_never_bought_has_every_month_and_becomes_inactive(after_batches) -> None:
    _, warehouse = after_batches
    sql = """
        select count(*)
            , cast(min(month_start_date) filter (where is_eligible) as varchar)
            , count(*) filter (where is_active)
        from marts.fct_member_month where member_id = 'M90002'
    """
    # D8: enrolled 15 January 2025; January 2025 to September 2026 is 21 months; eligible from 15 April.
    assert query(warehouse, sql) == [(21, "2025-04-01", 0)]


def test_eligible_members_who_never_bought_count_as_inactive(after_batches) -> None:
    _, warehouse = after_batches
    sql = """
        with never_bought as (
            select member_id from marts.dim_member
            where member_id not in (select member_id from marts.fct_sales_line where member_id is not null)
        )
        select count(*), count(*) filter (where m.is_active)
        from marts.fct_member_month m join never_bought using (member_id)
        where m.is_eligible and m.month_start_date = '2026-09-01'
    """
    eligible, active = query(warehouse, sql)[0]
    assert eligible > 0 and active == 0  # A3


def test_a_corrected_enrolment_moves_the_member_between_cohorts(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = (
        "select cast(min(month_start_date) as varchar) from marts.fct_member_month where member_id = 'M90004'"
    )
    assert query(after_1, sql) == [("2025-03-01",)] and query(after_2, sql) == [("2025-02-01",)]  # D12
    cohorts = """
        select cast(cohort_month_start_date as varchar), cohort_member_count from marts.agg_retention_cohort
        where cohort_month_start_date in ('2025-02-01', '2025-03-01') and months_since_enrolment = 3 order by 1
    """
    (feb_1, feb_count_1), (mar_1, mar_count_1) = query(after_1, cohorts)
    (feb_2, feb_count_2), (mar_2, mar_count_2) = query(after_2, cohorts)
    assert (feb_count_2 - feb_count_1, mar_count_2 - mar_count_1) == (1, -1)


def test_a_member_enrolled_days_ago_is_not_yet_eligible_and_their_cohort_is_immature(after_batches) -> None:
    _, warehouse = after_batches
    member = """
        select is_eligible, is_active, qualifying_purchase_count
        from marts.fct_member_month where member_id = 'M90006'
    """
    assert query(warehouse, member) == [(False, True, 1)]  # D15
    cohort = """
        select is_mature, repeat_purchase_rate from marts.agg_repeat_purchase_cohort
        where cohort_month_start_date = '2026-09-01'
    """
    assert query(warehouse, cohort) == [(False, None)]


def test_a_cohort_matures_when_its_window_closes_not_before(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = """
        select is_mature, repeat_purchase_rate is null from marts.agg_repeat_purchase_cohort
        where cohort_month_start_date = '2026-07-01'
    """
    # July's last window closes on 29 September: open at the 16 September as-of date, closed at 30 September.
    assert query(after_1, sql) == [(False, True)]
    assert query(after_2, sql) == [(True, False)]


def test_retention_cells_exist_only_for_closed_months(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = "select cast(max(measured_month_start_date) as varchar) from marts.agg_retention_cohort"
    assert query(after_1, sql) == [("2026-08-01",)]  # September is still open at 16 September
    assert query(after_2, sql) == [("2026-09-01",)]

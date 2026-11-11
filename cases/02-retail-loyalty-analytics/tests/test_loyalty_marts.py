"""Loyalty marts: events never multiply revenue, and reward status follows the evidence, not the status column."""

from conftest import query


def test_three_events_on_one_purchase_count_its_revenue_once(after_batches) -> None:
    _, warehouse = after_batches
    sql = """
        select net_amount, points_earned, reward_redemption_count
        from marts.fct_transaction where transaction_id = 'T9000003'
    """
    assert query(warehouse, sql) == [(40, 90, 1)]  # D3: earn 40, bonus 50, one redemption
    events = "select count(*) from marts.fct_loyalty_event where transaction_id = 'T9000003'"
    assert query(warehouse, events) == [(3,)]


def test_a_redemption_missing_from_the_status_column_is_resolved_from_the_event_log(after_batches) -> None:
    for warehouse in after_batches:
        sql = """
            select source_status, resolved_status, redeemed_transaction_id, status_conflict_reason
            from marts.fct_reward_entitlement where entitlement_id = 'R9000002'
        """
        assert query(warehouse, sql) == [
            ("issued", "redeemed", "T9000018", "redemption_missing_from_status")
        ]  # D13


def test_an_entitlement_stuck_in_issued_after_expiry_is_resolved_as_expired(after_batches) -> None:
    for warehouse in after_batches:
        sql = """
            select source_status, resolved_status, is_resolved, status_conflict_reason
            from marts.fct_reward_entitlement where entitlement_id = 'R9000003'
        """
        assert query(warehouse, sql) == [("issued", "expired", True, "expiry_missing_from_status")]  # D14


def test_only_the_planted_entitlements_disagree_with_their_status_column(after_batches) -> None:
    _, warehouse = after_batches
    sql = "select entitlement_id from marts.fct_reward_entitlement where has_status_conflict order by 1"
    assert query(warehouse, sql) == [("R9000002",), ("R9000003",)]


def test_open_entitlements_are_outside_the_redemption_denominator(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = """
        select count(*) filter (where resolved_status = 'open' and is_resolved)
            , count(*) filter (where resolved_status in ('redeemed', 'expired') and not is_resolved)
            , count(*) filter (where resolved_status = 'open')
        from marts.fct_reward_entitlement
    """
    open_after_1 = query(after_1, sql)[0]
    open_after_2 = query(after_2, sql)[0]
    assert open_after_1[:2] == (0, 0) and open_after_2[:2] == (0, 0)
    # Rewards issued in the last weeks are still open; none of them may count as unused.
    assert open_after_1[2] > 0 and open_after_2[2] > 0

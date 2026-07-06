from server import ApplicationRequestV2, make_decision_v2
from bki_parse import BkiFeatures

BASE = dict(
    last_name="Иванов", first_name="Иван", phone="+79991234567",
    email="ivan@example.ru", passport="1234567890",
    region="Москва", loan_purpose="покупка", amount=100000,
)


def _payload(birth_date="2000-05-15"):
    return ApplicationRequestV2(**BASE, birth_date=birth_date)


def test_approved_when_adult_and_clean():
    d = make_decision_v2(_payload(), in_black_list=False, black_list_check_failed=False)
    assert d["status"] == "approved"
    assert d["reasons"] == []


def test_declined_when_underage():
    d = make_decision_v2(_payload(birth_date="2015-01-01"))
    assert d["status"] == "declined"
    assert any("меньше" in r for r in d["reasons"])


def test_declined_when_in_black_list():
    d = make_decision_v2(_payload(), in_black_list=True)
    assert d["status"] == "declined"
    assert any("чёрном списке" in r for r in d["reasons"])


def test_declined_when_check_failed():
    d = make_decision_v2(_payload(), black_list_check_failed=True)
    assert d["status"] == "declined"
    assert any("Не удалось проверить" in r for r in d["reasons"])


def test_no_upper_age_limit():
    # 40 лет — для v2 это НЕ повод к отказу (верхней границы нет).
    d = make_decision_v2(_payload(birth_date="1985-01-01"))
    assert d["status"] == "approved"


def _bki(has_current_delinquency: bool) -> BkiFeatures:
    return BkiFeatures(
        score=650, n_contracts=1, has_writeoff=has_current_delinquency,
        has_current_delinquency=has_current_delinquency,
        overdue_amount_kop=100 if has_current_delinquency else 0,
        max_dpd=6 if has_current_delinquency else 0, n_late=0,
        debt_load_kop=0, inq_30=0, inq_90=0, inq_365=0,
    )


def test_declined_on_bki_current_delinquency():
    d = make_decision_v2(_payload(), bki=_bki(True))
    assert d["status"] == "declined"
    assert any("просрочка или списание" in r for r in d["reasons"])


def test_approved_with_clean_bki():
    d = make_decision_v2(_payload(), bki=_bki(False))
    assert d["status"] == "approved"


def test_no_history_is_not_a_rejection():
    # «Истории нет» (Код=3) — валидный ответ бюро, не сбой: bki=None без флага.
    d = make_decision_v2(_payload(), bki=None)
    assert d["status"] == "approved"
    assert d["reasons"] == []


def test_declined_when_bki_unavailable():
    # fail-closed: бюро недоступно после ретрая — отказ (как у чёрного списка).
    d = make_decision_v2(_payload(), bki=None, bki_check_failed=True)
    assert d["status"] == "declined"
    assert any("Не удалось проверить кредитную историю" in r for r in d["reasons"])


def test_declined_on_active_loan():
    d = make_decision_v2(_payload(), has_active_loan=True)
    assert d["status"] == "declined"
    assert any("Активный заём" in r for r in d["reasons"])


def test_declined_on_prior_default():
    d = make_decision_v2(_payload(), has_prior_default=True)
    assert d["status"] == "declined"
    assert any("невозврат" in r for r in d["reasons"])


def test_reasons_accumulate_across_rules():
    # Причины не перетирают друг друга: несовершеннолетний + невозврат = 2 причины.
    d = make_decision_v2(_payload(birth_date="2015-01-01"), has_prior_default=True)
    assert d["status"] == "declined"
    assert len(d["reasons"]) == 2

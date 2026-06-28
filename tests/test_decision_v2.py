from server import ApplicationRequestV2, make_decision_v2

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

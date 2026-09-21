"""Test ringan untuk joki.cli — fokus pada _classify_task & helper non-LLM."""


def test_classify_task_coding():
    import joki.cli as c
    assert c._classify_task("tolong buatkan script python") == c.MODE_CODE


def test_classify_task_sysadmin():
    import joki.cli as c
    assert c._classify_task("konfigurasi nginx dan restart service") == c.MODE_SYSADMIN


def test_classify_task_security():
    import joki.cli as c
    assert c._classify_task("scan port 192.168.1.1") == c.MODE_SECURITY


def test_classify_task_general():
    import joki.cli as c
    assert c._classify_task("halo apa kabar") == c.MODE_GENERAL

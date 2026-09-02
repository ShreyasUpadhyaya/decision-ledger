import mongomock
from app.audit import log_audit

def test_log_audit():
    client = mongomock.MongoClient()
    db = client.decision_ledger_test
    
    log_audit(db, "test.ruleset", "CREATE_VERSION", "test_actor", 1, {"detail": "info"})
    
    doc = db.audit_log.find_one({"ruleset_id": "test.ruleset"})
    assert doc is not None
    assert doc["action"] == "CREATE_VERSION"
    assert doc["actor"] == "test_actor"
    assert doc["version"] == 1
    assert doc["details"]["detail"] == "info"
    assert "timestamp" in doc

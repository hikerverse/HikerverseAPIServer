from hikerversedb.database import SessionFactory


def get_db():
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()

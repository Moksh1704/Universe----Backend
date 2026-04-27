from app.database import SessionLocal
from app.models import User
from app.auth.utils import hash_password

db = SessionLocal()
user = db.query(User).filter(User.email == "sainaimisha.17@gmail.com").first()

print(f"Current hash length: {len(user.hashed_password)}")  # will print 59

user.hashed_password = hash_password("Mok@14")  # her known password
user.is_first_login = False                      # she already changed it once — preserve this
db.commit()
db.close()
print("Done.")
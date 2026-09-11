import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
URL, KEY = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]

def session(email, password):
    c = create_client(URL, KEY)
    c.auth.sign_in_with_password({"email": email, "password": password})
    return c

def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}  (got {actual}, want {expected})")
    return ok

a = session("patient.a@test.com", "TestPass123!")
b = session("patient.b@test.com", "TestPass123!")
d = session("doctor.c@test.com",  "TestPass123!")

a_id = a.auth.get_user().user.id
results = []

# A writes one row
a.table("session_logs").insert({
    "user_id": a_id, "mode": "reflex",
    "accuracy_rate": 87.5, "reaction_time_ms_avg": 412
}).execute()

results.append(check("A sees own rows",
    len(a.table("session_logs").select("id").execute().data) >= 1, True))

results.append(check("B sees none of A's",
    len(b.table("session_logs").select("id").execute().data), 0))

results.append(check("Doctor sees all",
    len(d.table("session_logs").select("id").execute().data) >= 1, True))

try:
    b.table("session_logs").insert({"user_id": a_id, "mode": "focus"}).execute()
    results.append(check("Forged user_id rejected", False, True))
except Exception:
    results.append(check("Forged user_id rejected", True, True))

b.table("user_roles").update({"role": "doctor"}).eq("user_id",
    b.auth.get_user().user.id).execute()
results.append(check("B cannot self-promote",
    b.table("user_roles").select("role").execute().data[0]["role"], "patient"))

print("\nALL PASS" if all(results) else "\nFAILURES — do not proceed")
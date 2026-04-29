import bcrypt

password = "123456"
stored_hash = "$2b$12$oMjWUx73I1t5R8/A/Jkc/OSgcHEOwJ/K/rlMTjkIYQW8gkLiDQXla"

try:
    is_valid = bcrypt.checkpw(
        password.encode('utf-8'),
        stored_hash.encode('utf-8')
    )
    print(f"Password '{password}' valid for hash: {is_valid}")
except Exception as e:
    print(f"Error: {e}")

new_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
print(f"New hash for '{password}': {new_hash}")
is_valid_new = bcrypt.checkpw(password.encode('utf-8'), new_hash.encode('utf-8'))
print(f"New hash valid: {is_valid_new}")

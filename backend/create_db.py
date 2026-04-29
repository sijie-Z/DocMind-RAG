
import os
import pymysql

host = os.getenv("DB_HOST", "localhost")
port = int(os.getenv("DB_PORT", "3306"))
user = os.getenv("DB_USER", "root")
password = os.getenv("DB_PASSWORD", "")
db_name = os.getenv("DB_NAME", "paicongming_db")

try:
    print(f"Connecting to MySQL at {host}:{port} as {user}...")
    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password
    )
    
    with connection.cursor() as cursor:
        print(f"Creating database '{db_name}' if it does not exist...")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        print("Database created successfully or already exists.")
        
    connection.close()
    print("Done.")

except Exception as e:
    print(f"Error: {e}")


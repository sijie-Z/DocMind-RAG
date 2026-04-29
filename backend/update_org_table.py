
import os
import pymysql

def update_db():
    connection = pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        db=os.getenv("DB_NAME", "paicongming_db")
    )
    try:
        with connection.cursor() as cursor:
            # Check existing columns
            cursor.execute("SHOW COLUMNS FROM organizations")
            columns = [col[0] for col in cursor.fetchall()]
            
            if 'color' not in columns:
                print("Adding 'color' column...")
                cursor.execute("ALTER TABLE organizations ADD COLUMN color VARCHAR(20) DEFAULT '#18a058' AFTER description")
            
            if 'parent_id' not in columns:
                print("Adding 'parent_id' column...")
                cursor.execute("ALTER TABLE organizations ADD COLUMN parent_id INT NULL AFTER is_private")
                cursor.execute("ALTER TABLE organizations ADD CONSTRAINT fk_parent_id FOREIGN KEY (parent_id) REFERENCES organizations(id)")
            
            if 'level' not in columns:
                print("Adding 'level' column...")
                cursor.execute("ALTER TABLE organizations ADD COLUMN level INT DEFAULT 1 AFTER parent_id")
            
            if 'sort_order' not in columns:
                print("Adding 'sort_order' column...")
                cursor.execute("ALTER TABLE organizations ADD COLUMN sort_order INT DEFAULT 0 AFTER level")
            
            connection.commit()
            print("Database update completed successfully.")
    except Exception as e:
        print(f"Error updating database: {e}")
        connection.rollback()
    finally:
        connection.close()

if __name__ == "__main__":
    update_db()


import asyncio
import sys
import os

# Add backend to sys.path
sys.path.append(os.getcwd())

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.services.auth_service import AuthService
from app.models.user import User
from app.models.organization import Organization
from app.models.document import Document
from app.models.chat import ChatSession

async def seed():
    async with AsyncSessionLocal() as db:
        auth_service = AuthService()
        username = "admin"
        
        print(f"Checking user {username}...")
        user = await auth_service.get_user_by_username(db, username)
        
        if not user:
            print(f"Creating user {username}...")
            # Create user without org first
            user = await auth_service.create_user(
                db,
                username=username,
                email="admin@example.com",
                password="password123",
                full_name="Administrator",
                organization_id=None,
                role="admin",
            )
            print(f"User {username} created. ID: {user.id}")
        else:
            print(f"User {username} exists. ID: {user.id}")

        # 2. Check/Create Organization
        print("Checking organization...")
        result = await db.execute(select(Organization).filter_by(id=1))
        org = result.scalars().first()
        
        if not org:
            print("Creating default organization...")
            org = Organization(
                name="Default Org",
                description="Default Organization",
                owner_id=user.id # Set owner
            )
            db.add(org)
            await db.commit()
            await db.refresh(org)
            print(f"Organization created: {org.id}")
        else:
            print("Default organization exists.")
        
        # 3. Update user's org if missing
        if not user.organization_id:
            user.organization_id = org.id
            db.add(user)
            await db.commit()
            print(f"User organization set to {org.id}")

if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except Exception as e:
        print(f"Script failed: {e}")

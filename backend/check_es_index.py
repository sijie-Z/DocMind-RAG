import asyncio
from app.core.elasticsearch import get_elasticsearch
from app.core.config import settings

async def check_index():
    print(f"Connecting to Elasticsearch at {settings.ELASTICSEARCH_HOSTS}...")
    es = await get_elasticsearch()
    
    index_name = settings.ELASTICSEARCH_INDEX_NAME
    
    try:
        # Check if index exists
        exists = await es.indices.exists(index=index_name)
        print(f"Index '{index_name}' exists: {exists}")
        
        if exists:
            # Count documents
            count = await es.count(index=index_name)
            print(f"Total documents in index: {count['count']}")
            
            # Search for latest documents
            resp = await es.search(
                index=index_name,
                query={"match_all": {}},
                size=2,
                sort=[{"created_at": {"order": "desc"}}],
            )
            
            print("\n--- Latest 2 Documents ---")
            for hit in resp['hits']['hits']:
                source = hit['_source']
                print(f"ID: {hit['_id']}")
                print(f"Content Preview: {source.get('content', '')[:100]}...")
                print(f"Vector Length: {len(source.get('vector', []))}")
                print("-" * 30)
        else:
            print("Index does not exist yet. Upload a document to create it.")
            
    except Exception as e:
        print(f"Error checking Elasticsearch: {e}")
    finally:
        await es.close()

if __name__ == "__main__":
    asyncio.run(check_index())

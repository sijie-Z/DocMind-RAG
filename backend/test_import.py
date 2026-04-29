try:
    import unstructured
    from unstructured.partition.pdf import partition_pdf
    print("success")
except ImportError as e:
    print(f"ImportError: {e}")
except Exception as e:
    print(f"Exception: {e}")

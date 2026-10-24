"""Project-wide settings: paths, data source, and analysis thresholds."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
DB_PATH = ROOT / "data" / "processed" / "olist.duckdb"

# --- Anomaly rules (decision D6) ------------------------------------------
# A segment is flagged only when all three hold.
MIN_N = 30          # delivered (or reviewed, etc.) orders in BOTH periods
MIN_ABS_Z = 2.0     # two-proportion z-test on the change
MIN_DELTA_PP = 3.0  # absolute change in percentage points

# --- Data source -----------------------------------------------------------
# Public Hugging Face mirror of the Kaggle "Brazilian E-Commerce Public Dataset
# by Olist" (CC BY-NC-SA 4.0). Pinned to one commit so the data can't change
# under us. The checksums were cross-checked against two other independent
# mirrors and against the Git LFS checksums published for the large files.
SOURCE_REPO = "bulutttt/olist-raw-data"
SOURCE_REVISION = "5acf1f37d7197d4979e714721a1ec13a7c3da728"
SOURCE_URL = f"https://huggingface.co/datasets/{SOURCE_REPO}/resolve/{SOURCE_REVISION}"

# raw table name -> (file name, sha256). Geolocation is deliberately not loaded:
# v1 uses state codes for regions, and the file is 61 MB.
SOURCE_FILES = {
    "raw_customers": ("olist_customers_dataset.csv", "983a422239e1712ded753b3bf9ecf47dc73f144d306029dcfa99e70a226883d2"),
    "raw_order_items": ("olist_order_items_dataset.csv", "0bc4d068c4fe38cbb01bd90e8746e3c613fe7b4baef75fab7b0e329701c3e279"),
    "raw_order_payments": ("olist_order_payments_dataset.csv", "4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0"),
    "raw_order_reviews": ("olist_order_reviews_dataset.csv", "012b61c7593e34f51fa614efdf802b9c7056ce6aae5307ddb93236e7cfc797d7"),
    "raw_orders": ("olist_orders_dataset.csv", "8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf"),
    "raw_products": ("olist_products_dataset.csv", "3e6569628a17fbc75fd206ee357b59e20364b9afa90f5b6cd5b4d624c58aa9cc"),
    "raw_sellers": ("olist_sellers_dataset.csv", "1f643d2b950373b85735e7794b20986f528d7a000432e7c6f9bcbb44d0846a0e"),
    "raw_category_translation": ("product_category_name_translation.csv", "a81f0d1f27b27e7293f761bc79e3ce8f348ee39c4b3ed3e49bde38f478586278"),
}

"""数据文件。"""

DATA_FILES_DDL = """
CREATE TABLE IF NOT EXISTS data_files (
  id            VARCHAR(100) PRIMARY KEY,
  original_name TEXT NOT NULL,
  mime_type     TEXT,
  size_bytes    BIGINT NOT NULL,
  storage_path  TEXT NOT NULL,
  uploaded_by   VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
  uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_data_files_uploaded_at
  ON data_files(uploaded_at DESC);
"""

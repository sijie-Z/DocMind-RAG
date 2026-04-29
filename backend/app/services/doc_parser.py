import os
import re
import shutil
import tempfile
import logging
import subprocess
import zipfile
from typing import List, Optional
from langchain_community.document_loaders import Docx2txtLoader, TextLoader, UnstructuredFileLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document as LangchainDocument
import pdfplumber

logger = logging.getLogger(__name__)


class DocParser:
    """强化版文档解析器：支持多格式兜底与更稳定切分"""

    def __init__(self):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", "。", "！", "？", " ", ""]
        )

    def parse_and_chunk(self, file_path: str) -> List[LangchainDocument]:
        try:
            ext = os.path.splitext(file_path)[1].lower()

            if ext == '.pdf':
                return self._process_pdf(file_path)

            if ext == '.doc':
                return self._process_doc(file_path)

            if ext == '.docx':
                return self._process_docx(file_path)

            loader = self._get_loader(file_path)
            if not loader:
                logger.warning(f"No suitable loader for file: {file_path}")
                return []

            logger.info(f"Loading document: {file_path}")
            raw_docs = loader.load()
            cleaned = self._clean_docs(raw_docs)
            return self._smart_split(cleaned, file_path)

        except Exception as e:
            logger.error(f"Error parsing document {file_path}: {e}", exc_info=True)
            raise

    def _process_docx(self, file_path: str) -> List[LangchainDocument]:
        """.docx 先做文件健康检查，避免 zip 损坏导致误报解析失败。"""
        if not zipfile.is_zipfile(file_path):
            logger.warning(f"Invalid .docx (not a zip package): {file_path}")
            return []

        try:
            loader = Docx2txtLoader(file_path)
            docs = loader.load()
            docs = self._clean_docs(docs)
            return self._smart_split(docs, file_path)
        except Exception as e:
            logger.warning(f"Docx2txt failed for {file_path}: {e}, fallback to Unstructured")
            try:
                loader = UnstructuredFileLoader(file_path)
                docs = loader.load()
                docs = self._clean_docs(docs)
                return self._smart_split(docs, file_path)
            except Exception as ex:
                logger.error(f"All .docx parsers failed for {file_path}: {ex}")
                return []

    def _process_doc(self, file_path: str) -> List[LangchainDocument]:
        """.doc 解析链路（Windows 优先）：antiword -> soffice 转 docx -> unstructured"""
        text = ""

        # 1) antiword（若环境有）
        try:
            out = subprocess.check_output(["antiword", file_path], stderr=subprocess.STDOUT, timeout=25)
            text = out.decode("utf-8", errors="ignore")
            logger.info(f"Parsed .doc with antiword: {file_path}")
        except Exception:
            logger.warning(f"antiword unavailable or failed for {file_path}")

        if text.strip():
            doc = LangchainDocument(page_content=self._clean_text(text), metadata={"source": file_path})
            return self._smart_split([doc], file_path)

        # 2) soffice 转换 doc -> docx（Windows 上成功率高）
        try:
            soffice_cmd = shutil.which("soffice")
            if soffice_cmd:
                tmp_dir = tempfile.mkdtemp(prefix="doc_convert_")
                try:
                    subprocess.check_output(
                        [soffice_cmd, "--headless", "--convert-to", "docx", "--outdir", tmp_dir, file_path],
                        stderr=subprocess.STDOUT,
                        timeout=45,
                    )
                    converted = os.path.join(tmp_dir, os.path.splitext(os.path.basename(file_path))[0] + ".docx")
                    if os.path.exists(converted):
                        docs = Docx2txtLoader(converted).load()
                        docs = self._clean_docs(docs)
                        if docs:
                            logger.info(f"Parsed .doc via soffice->docx: {file_path}")
                            return self._smart_split(docs, file_path)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            else:
                logger.warning("soffice not found in PATH, skip doc->docx conversion")
        except Exception as e:
            logger.warning(f"soffice convert failed for {file_path}: {e}")

        # 3) 兜底：Unstructured
        try:
            loader = UnstructuredFileLoader(file_path)
            docs = loader.load()
            docs = self._clean_docs(docs)
            if docs:
                logger.info(f"Parsed .doc with Unstructured fallback: {file_path}")
                return self._smart_split(docs, file_path)
        except Exception as e:
            logger.error(f"All .doc parsers failed for {file_path}: {e}")

        return []

    def _process_pdf(self, file_path: str) -> List[LangchainDocument]:
        logger.info(f"Using pdfplumber for PDF parsing: {file_path}")
        full_text = []

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                table_texts = []
                if tables:
                    for table in tables:
                        table_md = self._format_table_to_markdown(table)
                        if table_md:
                            table_texts.append(table_md)

                page_text = page.extract_text() or ""
                page_text = self._clean_text(page_text)

                if table_texts:
                    full_text.append(f"--- Page {page_num + 1} Tables ---\n" + "\n".join(table_texts))
                if page_text:
                    full_text.append(f"--- Page {page_num + 1} Text ---\n" + page_text)

        combined_text = "\n\n".join(full_text).strip()
        if not combined_text:
            return []

        doc = LangchainDocument(page_content=combined_text, metadata={"source": file_path})
        if len(combined_text) < 2000:
            return [doc]
        return self.text_splitter.split_documents([doc])

    def _format_table_to_markdown(self, table: List[List[Optional[str]]]) -> str:
        if not table or not any(table):
            return ""

        markdown_lines = []
        for i, row in enumerate(table):
            processed_row = [self._clean_text(str(cell) if cell is not None else "") for cell in row]
            markdown_lines.append("| " + " | ".join(processed_row) + " |")
            if i == 0:
                markdown_lines.append("| " + " | ".join(["---"] * len(row)) + " |")
        return "\n".join(markdown_lines)

    def _smart_split(self, docs: List[LangchainDocument], file_path: str) -> List[LangchainDocument]:
        total_length = sum(len(d.page_content or "") for d in docs)
        if total_length < 800:
            return docs
        logger.info(f"Splitting document into chunks: {file_path}")
        return self.text_splitter.split_documents(docs)

    def _clean_text(self, text: str) -> str:
        t = (text or "").replace("\x00", "").replace("\ufeff", "")
        t = t.replace("\r\n", "\n").replace("\r", "\n")
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    def _clean_docs(self, docs: List[LangchainDocument]) -> List[LangchainDocument]:
        cleaned: List[LangchainDocument] = []
        for d in docs:
            content = self._clean_text(d.page_content)
            if content:
                cleaned.append(LangchainDocument(page_content=content, metadata=d.metadata or {}))
        return cleaned

    def _get_loader(self, file_path: str):
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.docx':
            return Docx2txtLoader(file_path)
        if ext == '.txt' or ext == '.md':
            return TextLoader(file_path, encoding='utf-8')
        try:
            return UnstructuredFileLoader(file_path)
        except Exception:
            return None


# 全局实例
doc_parser = DocParser()

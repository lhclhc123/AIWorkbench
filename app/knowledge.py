# -*- coding: utf-8 -*-
"""本地知识库：把一批文档切片建索引，支持按关键词/语义打分检索（RAG-lite）。

不引入向量库与嵌入模型——纯本地倒排索引 + BM25 简化打分：
- 中文按「单字 + 二元组」切词，英文按单词；
- 建好的索引存放在工作区 .index/knowledge.json，可增量重建。
"""
import os
import re
import json
import math
import time

INDEX_DIR = ".index"
INDEX_FILE = "knowledge.json"

CHUNK_SIZE = 900        # 每片字符数
CHUNK_OVERLAP = 120

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_STOP = set("的 了 和 是 在 我 有 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己 这 那 "
            "就 与 及 或 而 但 因为 所以 如果 什么 怎么 可以 这个 那个 the a an of to in is are and or for on "
            "with as at by be this that it from".split())


def tokenize(text):
    """混合中英文切词，返回 term 列表（中文二元组权重更高，用重复实现加权）。"""
    if not text:
        return []
    low = text.lower()
    toks = []
    for w in re.findall(r"[a-z0-9_]+", low):
        if len(w) >= 2 and w not in _STOP:
            toks.append(w)
    for run in re.findall("[" + _CJK + "]+", low):
        for ch in run:
            if ch not in _STOP:
                toks.append(ch)
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            toks.append(bg)
            toks.append(bg)      # 二元组加权
    return toks


def _chunks(text):
    text = (text or "").strip()
    if not text:
        return []
    out = []
    i = 0
    n = len(text)
    while i < n:
        seg = text[i:i + CHUNK_SIZE]
        out.append(seg)
        if i + CHUNK_SIZE >= n:
            break
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return out


class KnowledgeBase:
    def __init__(self, workspace_path: str):
        self.ws = workspace_path
        self.dir = os.path.join(workspace_path, INDEX_DIR)
        self.path = os.path.join(self.dir, INDEX_FILE)
        self._data = None

    # ---------- 存取 ----------
    def load(self):
        if self._data is not None:
            return self._data
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception:
            self._data = {"root": "", "built": 0, "chunks": []}
        return self._data

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        d = self.load()
        d["built"] = time.time()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    def stats(self):
        d = self.load()
        files = {c["p"] for c in d.get("chunks", [])}
        return {"chunks": len(d.get("chunks", [])), "files": len(files),
                "root": d.get("root") or "", "built": d.get("built") or 0}

    def clear(self):
        self._data = {"root": "", "built": 0, "chunks": []}
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
        except Exception:
            pass

    # ---------- 建索引 ----------
    def _extract(self, path):
        """把任意支持的文件抽成纯文本。"""
        from . import docread
        try:
            if docread.is_image(path):
                return ""
            if docread.is_supported(path):
                return docread.read_document(path, max_chars=200000)
        except Exception:
            return ""
        # 兜底：当作纯文本读
        try:
            if os.path.getsize(path) > 8 * 1024 * 1024:
                return ""
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    def build(self, root, max_files=2000, on_progress=None):
        """扫描 root 下的可读文件，建索引。返回统计 dict。"""
        from . import docread
        root = os.path.abspath(root)
        skip_dirs = {"node_modules", "__pycache__", ".git", ".index", "$RECYCLE.BIN",
                     "System Volume Information", "AppData", ".venv", "venv",
                     "dist", "build", ".idea", ".vscode"}
        chunks = []
        files_done = 0
        files_skipped = 0
        for dp, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            for fn in files:
                if files_done + files_skipped >= max_files:
                    break
                p = os.path.join(dp, fn)
                ext = docread.ext_of(p)
                if ext not in docread.TEXT_EXTS and not docread.is_supported(p):
                    files_skipped += 1
                    continue
                if ext not in docread.TEXT_EXTS:
                    try:
                        if os.path.getsize(p) > 20 * 1024 * 1024:
                            files_skipped += 1
                            continue
                    except Exception:
                        files_skipped += 1
                        continue
                text = self._extract(p)
                if not text or len(text.strip()) < 20:
                    files_skipped += 1
                    continue
                rel = os.path.relpath(p, root)
                for ci, seg in enumerate(_chunks(text)):
                    chunks.append({"p": rel, "a": p, "i": ci,
                                   "t": seg, "n": len(seg)})
                files_done += 1
                if on_progress and files_done % 20 == 0:
                    on_progress(files_done, len(chunks))
        # 统计词频
        for c in chunks:
            tf = {}
            for t in tokenize(c["t"]):
                tf[t] = tf.get(t, 0) + 1
            c["f"] = tf
        self._data = {"root": root, "built": time.time(), "chunks": chunks}
        self.save()
        return {"files": files_done, "skipped": files_skipped,
                "chunks": len(chunks), "root": root}

    def add_file(self, path):
        """把单个文件加进索引（已存在则先替换）。"""
        from . import docread
        path = os.path.abspath(path)
        text = self._extract(path)
        if not text:
            return 0
        d = self.load()
        d["chunks"] = [c for c in d.get("chunks", []) if c.get("a") != path]
        added = 0
        for ci, seg in enumerate(_chunks(text)):
            tf = {}
            for t in tokenize(seg):
                tf[t] = tf.get(t, 0) + 1
            d["chunks"].append({"p": os.path.basename(path), "a": path, "i": ci,
                                "t": seg, "n": len(seg), "f": tf})
            added += 1
        self.save()
        return added

    # ---------- 检索 ----------
    def search(self, query, topk=5):
        """BM25 简化打分检索，返回 [{path, full, score, snippet}]。"""
        d = self.load()
        chunks = d.get("chunks", [])
        if not chunks:
            return []
        q_terms = tokenize(query)
        if not q_terms:
            return []
        q_set = set(q_terms)
        N = len(chunks)
        df = {}
        for c in chunks:
            f = c.get("f") or {}
            for t in q_set:
                if t in f:
                    df[t] = df.get(t, 0) + 1
        avg_len = sum(c.get("n", 1) for c in chunks) / max(N, 1)
        k1, b = 1.5, 0.75
        scored = []
        for c in chunks:
            f = c.get("f") or {}
            dl = c.get("n", 1)
            s = 0.0
            hit = 0
            for t in q_set:
                tf = f.get(t)
                if not tf:
                    continue
                hit += 1
                idf = math.log(1 + (N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
                s += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_len))
            if s > 0:
                # 命中词越多越相关
                s *= (1 + 0.15 * hit)
                scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, c in scored[:topk]:
            out.append({
                "path": c.get("p", ""),
                "full": c.get("a", ""),
                "part": c.get("i", 0),
                "score": round(s, 3),
                "snippet": _snippet(c.get("t", ""), q_set),
            })
        return out


def _snippet(text, q_set, width=260):
    """截取命中词附近的片段，便于阅读。"""
    if not text:
        return ""
    low = text.lower()
    pos = -1
    for t in sorted(q_set, key=len, reverse=True):
        i = low.find(t)
        if i >= 0:
            pos = i
            break
    if pos < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    seg = text[start:end].replace("\n", " ")
    seg = re.sub(r"\s{2,}", " ", seg)
    return ("…" if start > 0 else "") + seg + ("…" if end < len(text) else "")

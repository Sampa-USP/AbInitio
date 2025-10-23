#!/usr/bin/env python3
import argparse, os, shutil, json, html
from pathlib import Path
import subprocess
from datetime import datetime
import sys
import re, json, html

# ============================= Helpers =============================

IGNORE_DIRS = {
    ".git", ".github", ".venv", "venv", "__pycache__",
    "node_modules", ".script", "site"
}

def copy_tree(src_dir: Path, dst_dir: Path):
    """
    Copia todo o conteúdo de src_dir para dst_dir (se existir).
    Mantém estrutura, sobrescreve arquivos.
    """
    if not src_dir.exists():
        return
    for root, dirs, files in os.walk(src_dir):
        rel = Path(root).relative_to(src_dir)
        out_root = dst_dir / rel
        out_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            src_f = Path(root) / f
            dst_f = out_root / f
            shutil.copy2(src_f, dst_f)

def load_template_index(template_dir: Path) -> str:
    """
    Lê template/index.html. Lança erro claro se não existir.
    """
    index_path = template_dir / "index.html"
    if not index_path.exists():
        raise FileNotFoundError(f"Template missing: {index_path}")
    return index_path.read_text(encoding="utf-8")

def render_index(index_src: str, title: str, nb_count: int, tree: dict) -> str:
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    safe_json = json.dumps(tree, ensure_ascii=False).replace("</", "<\\/")  # evita fechar <script>

    rep = {
        r"\{\{\s*TITLE\s*\}\}": html.escape(title),
        r"\{\{\s*TIMESTAMP\s*\}\}": timestamp,
        r"\{\{\s*NBCOUNT\s*\}\}": str(nb_count),
        r"\{\{\s*TREE_JSON\s*\}\}": safe_json,
    }
    out = index_src
    for pattern, value in rep.items():
        out = re.sub(pattern, lambda m, v=value: v, out)  # <— literal
    return out

# ====================== Núcleo de varredura/build ======================

#     return root, nb_count
import sys, subprocess
from pathlib import Path

def collect_tree(src: Path, out: Path, execute: bool):
    """
    Varre src; converte apenas .ipynb -> .html em out.
    - Arquivos que não sejam .ipynb são ignorados.
    - Diretórios sem nenhum notebook são removidos da árvore.
    """
    nb_count = 0
    root = {"type": "dir", "name": src.name, "path": "", "children": []}
    dir_map = {str(src.resolve()): root}

    for path in sorted(src.rglob("*")):
        if out in path.parents or path == out:
            continue
        rel_parts = path.relative_to(src).parts
        if not rel_parts:
            continue

        # Garante nós de diretório
        cur = src
        parent_node = root
        for i, p in enumerate(rel_parts[:-1] if not path.is_dir() else rel_parts):
            cur = cur / p
            key = str(cur.resolve())
            if key not in dir_map:
                node = {"type": "dir", "name": p, "path": str(Path(*rel_parts[: i + 1])), "children": []}
                parent_node["children"].append(node)
                dir_map[key] = node
            parent_node = dir_map[key]

        # Se for diretório, só garante hierarquia
        if path.is_dir():
            continue

        # Se não for .ipynb → ignora
        if path.suffix.lower() != ".ipynb":
            continue

        # Converte notebook
        rel = path.relative_to(src)
        file_node = {"type": "file", "name": rel.name, "path": str(rel)}
        nb_count += 1

        out_html = (out / rel).with_suffix(".html")
        out_html.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable, "-m", "nbconvert", "--to", "html",
            "--HTMLExporter.embed_images=True", 
            "--output", out_html.name, "--output-dir", str(out_html.parent), str(path)
        ]
        if execute:
            cmd.append("--execute")
        subprocess.run(cmd, check=True)

        file_node["nb_html"] = str(out_html.relative_to(out)).replace(os.sep, "/")

        parent_key = str(path.parent.resolve())
        dir_map[parent_key]["children"].append(file_node)

    # --- remove diretórios vazios ---
    def prune_empty_dirs(node):
        if node["type"] == "file":
            return node, True
        new_children = []
        has_ipynb = False
        for ch in node.get("children", []):
            pruned, child_has_ipynb = prune_empty_dirs(ch)
            if pruned:
                new_children.append(pruned)
            has_ipynb = has_ipynb or child_has_ipynb
        node["children"] = new_children
        return (node if has_ipynb else None), has_ipynb

    root, _ = prune_empty_dirs(root)
    if root is None:
        root = {"type": "dir", "name": src.name, "path": "", "children": []}

    return root, nb_count


def build_static_site(src: Path, out: Path, template_dir: Path, title: str, execute: bool):
    tree, nb_count = collect_tree(src, out, execute)

    out.mkdir(parents=True, exist_ok=True)
    # páginas a gerar: nome do arquivo -> injeta TREE_JSON?
    pages = [
        ("index.html", False),     # Home
        ("software.html", True),   # Software (com árvore)
        ("publications.html", False),
        ("research.html", False),
    ]
    for fname, needs_tree in pages:
        page_path = template_dir / fname
        if not page_path.exists():  # ignora se não existir
            continue
        src_html = page_path.read_text(encoding="utf-8")
        html_doc = render_tokens(src_html, title, nb_count, tree if needs_tree else None)
        (out / fname).write_text(html_doc, encoding="utf-8")

    # assets
    copy_tree(template_dir / "css", out / "css")
    copy_tree(template_dir / "assets", out / "assets")
    copy_tree(template_dir / "js", out / "js")

    return nb_count

# ================================ CLI ================================

def render_tokens(src: str, title: str, nb_count: int, tree: dict | None):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    rep = {
        r"\{\{\s*TITLE\s*\}\}": html.escape(title),
        r"\{\{\s*TIMESTAMP\s*\}\}": ts,
        r"\{\{\s*NBCOUNT\s*\}\}": str(nb_count),
    }
    out = src
    for pat, val in rep.items():
        out = re.sub(pat, lambda m, v=val: v, out)
    if tree is not None:
        safe_json = json.dumps(tree, ensure_ascii=False).replace("</", "<\\/")
        out = re.sub(r"\{\{\s*TREE_JSON\s*\}\}", lambda m, v=safe_json: v, out)
    return out

def main():
    ap = argparse.ArgumentParser(
        description="Gera um site estático a partir de notebooks .ipynb usando nbconvert e um template externo."
    )
    ap.add_argument("--src", type=str, required=True, help="Raiz do repositório (onde estão os notebooks)")
    ap.add_argument("--out", type=str, required=True, help="Diretório de saída do site estático (ex.: site/)")
    ap.add_argument("--template", type=str, required=True, help="Diretório do template (ex.: template/)")
    ap.add_argument("--title", type=str, default=None, help="Título a exibir no site (padrão: 'Notebooks Tree — <src.name>')")
    ap.add_argument("--execute", type=str, default="false", help="true/false: executar notebooks antes de converter")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    out = Path(args.out).resolve()
    template_dir = Path(args.template).resolve()
    execute = args.execute.lower() == "true"
    title = args.title or f"Notebooks Tree — {src.name}"

    nb_count = build_static_site(src, out, template_dir, title, execute)
    print(f"[OK] Gerado em {out} • notebooks convertidos: {nb_count}")

if __name__ == "__main__":
    main()
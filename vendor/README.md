# Tulpa source snapshot

Upstream: https://github.com/fumingyang2004/Tulpa

Pinned commit: `0090b27fad42428c72e5cbaa903a140ca6bd51d4` (MIT), including the Linux terminal/private-chat port. The complete tracked source snapshot is stored in `tulpa/`; no nested Git checkout, credentials or local chat database is included.

Local patches: `chatlocal/config.py` accepts `TULPA_ROOT` and creates that directory; `mcp_chat_media.py` recognizes RFC 6145 translated fake DNS addresses before the original authenticated DNS fallback. Private-address rejection remains enforced. All MCP, permissions, batching, planning, sender, receipts and learning logic is upstream code. See `tulpa-provenance.json` and the upstream `LICENSE` and `licenses/` notices.

To update: inspect the upstream diff and license, choose an exact commit, replace the clean snapshot, reapply the documented patches, update provenance, run the QQbot integration suite plus upstream protocol checks and real-model/QQ acceptance, then commit a new QQbot version. Do not overwrite the persistent `/data/tulpa` directory with source files or silently float to upstream HEAD.

The application uses the headless MCP and terminal core. It does not install upstream's optional Gradio desktop, DeepSeek investigation SDK or APSW native-history components. Their source and licenses remain in the snapshot; enabling these optional components requires separate dependencies and validation.

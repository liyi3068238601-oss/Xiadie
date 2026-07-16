"""K.5 扩展合成集校准：确定性 high 可用，纯 semantic 仍禁止升档。"""

THRESHOLD_VERSION = "knowledge-recall-thresholds-v2"
SOURCE_EVALUATION_PROTOCOL = "knowledge-recall-eval-v3"
SOURCE_FIXTURE_SHA256 = "1b9beb3fc3ff947243f3e14bb8f19778f9d68df4358b5fa23989586d51415a3d"
SOURCE_SAMPLE_COUNT = 52

# v3 有 30 个相关 dense 与 15 个无关 dense；无关最高 0.561615，高于相关最低
# 0.477699，类别不再可分。保留 v1 下限仅用于候选去噪，semantic 必须保持 medium。
SEMANTIC_CANDIDATE_MIN_SCORE = 0.472169
EXACT_TERM_HIGH_MIN_CHARS = 3
ENTITY_MEDIUM_MIN_CHARS = 2
SEMANTIC_AUTO_HIGH_ENABLED = False
# 只允许 exact_term_hit/source_conflict 等确定性 high；medium/low 永不真实注入。
AUTOMATIC_INJECTION_ENABLED = True

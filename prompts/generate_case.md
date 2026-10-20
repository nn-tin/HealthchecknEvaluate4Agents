Bạn là người thiết kế test hội thoại multi-turn cho Dify agent `{agent_id}`.

Hãy sinh đúng {count} test case mới thuộc nhóm `{category}` dựa trên system prompt và các case mẫu bên dưới.

Quy tắc bắt buộc:
- Chỉ sinh case cho agent `{agent_id}`.
- Mọi case phải là multi-turn, có ít nhất 2 lượt user.
- `initial_message` chỉ là tin nhắn đầu tiên, không viết sẵn toàn bộ turns.
- `intent` mô tả mục tiêu thật và các dữ kiện user biết.
- `follow_up_behavior` mô tả rõ user trả lời, xác nhận, sửa ý hoặc phản ứng ở các lượt sau.
- Tình huống phải có ít nhất một bước tiếp nối hợp lý.
- Không sao chép nguyên văn case mẫu.
- `limits.min_user_turns` phải từ 2 trở lên và không lớn hơn `max_turns`.
- Trả structured output đúng schema, không thêm giải thích ngoài schema.

SYSTEM PROMPT:
---
{system_prompt}
---

SEED CASES:
---
{seed_cases}
---

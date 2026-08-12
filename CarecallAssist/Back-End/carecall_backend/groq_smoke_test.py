import os

import groq
from groq import Groq


MODEL_ID = "openai/gpt-oss-120b"


def main() -> int:
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        print("[FAIL] GROQ_API_KEY 환경변수가 없습니다.")
        return 1

    try:
        with Groq(
            api_key=api_key,
            timeout=30.0,
            max_retries=1,
        ) as client:
            completion = client.chat.completions.create(
                model=MODEL_ID,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "당신은 API 연결 확인용 도우미입니다. "
                            "요청받은 문장만 한국어로 짧게 답하세요."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Groq API 연결 테스트입니다. "
                            "연결이 정상이라면 'Groq API 연결 성공'이라고 답하세요."
                        ),
                    },
                ],
                reasoning_effort="low",
                include_reasoning=False,
                max_completion_tokens=256,
                stream=False,
            )

        answer = completion.choices[0].message.content

        if not answer:
            print("[FAIL] API 호출은 완료됐지만 응답 내용이 비어 있습니다.")
            return 1

        print("[SUCCESS] Groq API 응답 수신")
        print(f"[MODEL] {completion.model}")
        print(f"[ANSWER] {answer}")

        usage = completion.usage
        if usage is not None:
            print(
                "[TOKENS] "
                f"prompt={usage.prompt_tokens}, "
                f"completion={usage.completion_tokens}, "
                f"total={usage.total_tokens}"
            )

        return 0

    except groq.AuthenticationError:
        print("[ERROR 401] API 키가 올바르지 않거나 사용할 수 없습니다.")
        return 1

    except groq.PermissionDeniedError:
        print("[ERROR 403] 해당 모델에 대한 프로젝트 권한이 없습니다.")
        return 1

    except groq.RateLimitError:
        print("[ERROR 429] Groq 무료 사용 한도 또는 요청 한도에 도달했습니다.")
        return 1

    except groq.APIConnectionError:
        print("[NETWORK ERROR] Groq API 서버에 연결하지 못했습니다.")
        return 1

    except groq.APIStatusError as error:
        print(f"[API ERROR] HTTP 상태 코드: {error.status_code}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

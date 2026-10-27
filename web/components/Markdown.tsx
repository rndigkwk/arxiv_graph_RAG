import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 답은 마크다운입니다(Streamlit도 그렇게 그렸습니다). 표가 자주 나오므로 GFM을 켭니다.
// HTML은 그리지 않습니다(rehype-raw 없음) - 모델이 쓴 글이 화면에 태그를 넣지 못합니다.
export function Markdown({ text, muted = false }: { text: string; muted?: boolean }) {
  return (
    <div className="answer-text" style={muted ? { color: "var(--ink-2)" } : undefined}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

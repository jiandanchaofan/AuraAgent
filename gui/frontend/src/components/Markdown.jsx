import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import 'katex/dist/katex.min.css'

// remark-math only recognizes $...$ / $$...$$ -- models often write LaTeX
// as \(...\) / \[...\] instead (both are common depending on how a request
// is phrased), so both conventions are normalized to the one remark-math
// understands before rendering. Left as plain text if nothing matches.
function normalizeMathDelimiters(text) {
  return text.replace(/\\\[([\s\S]+?)\\\]/g, (_, expr) => `$$${expr}$$`).replace(/\\\(([\s\S]+?)\\\)/g, (_, expr) => `$${expr}$`)
}

// Renders model-generated prose (final answers, thoughts, delegation
// results) as real markdown + LaTeX math -- remark-math/rehype-katex parse
// $...$ / $$...$$ into actual formulas, remark-gfm adds tables/strikethrough/
// task lists, rehype-highlight tags code blocks with per-token `hljs-*`
// classes (index.css supplies the actual colors -- scoped to just the
// answer-text classes, see its own comment, since a coloring scheme this
// bold would fight with Thought's deliberately muted/gray treatment). Raw
// HTML in the source is never parsed as markup (no rehype-raw plugin
// here), so it comes through as literal escaped text -- there's no XSS
// surface even though this text ultimately comes from an LLM (and, for a
// delegation's result, from a Worker).
export default function Markdown({ children, className }) {
  if (!children) return null
  return (
    <div className={className ? `md-content ${className}` : 'md-content'}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex, rehypeHighlight]}>
        {normalizeMathDelimiters(children)}
      </ReactMarkdown>
    </div>
  )
}

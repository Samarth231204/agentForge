const URL_PATTERN = /(https?:\/\/[^\s]+)/g;

/**
 * Splits response text around raw URLs so they render as real, clickable
 * links instead of inert text. Shared by the single-answer view and the
 * pipeline view — both can surface a Google OAuth consent URL, which is
 * useless to the user if it isn't clickable.
 */
export function renderWithLinks(text) {
  // URL_PATTERN has a capturing group, so split() interleaves the matched
  // URLs into the result at odd indices — checking index parity avoids
  // re-testing against the same stateful global-flag regex (whose lastIndex
  // would otherwise make repeated .test() calls unreliable).
  return String(text ?? "")
    .split(URL_PATTERN)
    .map((part, i) =>
      i % 2 === 1 ? (
        <a key={i} href={part} target="_blank" rel="noopener noreferrer">
          {part}
        </a>
      ) : (
        part
      )
    );
}

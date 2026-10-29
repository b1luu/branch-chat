interface ErrorNoticeProps {
  /** What failed, e.g. "Couldn't load the tree". */
  title: string;
  /** The underlying error message (already user-readable, see `ApiError`). */
  message: string;
  /** Shown as a "Try again" button when given. */
  onRetry?: (() => void) | undefined;
  /** Disables the retry button while a retry is in flight. */
  retrying?: boolean;
}

/**
 * The one error presentation used across the app: an alert with what failed,
 * why, and (when the action is safe to repeat) a "Try again" button, so a
 * transient failure never needs a page reload.
 */
export default function ErrorNotice({ title, message, onRetry, retrying = false }: ErrorNoticeProps) {
  return (
    <div role="alert" className="error-notice">
      <p>
        {title}: {message}
      </p>
      {onRetry && (
        <button type="button" onClick={onRetry} disabled={retrying}>
          {retrying ? "Retrying…" : "Try again"}
        </button>
      )}
    </div>
  );
}

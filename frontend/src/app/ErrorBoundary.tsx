import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, _info: ErrorInfo): void {
    console.error("Frontend render error:", error.name, error.message);
  }

  private reset = (): void => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <main className="error-boundary" role="alert">
          <div className="state-icon state-icon-error" aria-hidden="true">!</div>
          <h1>页面暂时无法显示</h1>
          <p>请重试；如果问题持续，请记录当前页面和请求编号。</p>
          <button type="button" className="button button-primary" onClick={this.reset}>重新加载</button>
        </main>
      );
    }

    return this.props.children;
  }
}

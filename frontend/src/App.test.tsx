import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App.tsx";
import { jsonResponse, mockFetch } from "./test/fetch.ts";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("renders the title and a loading state", () => {
    mockFetch(jsonResponse([]));
    render(<App />);
    expect(screen.getByRole("heading", { name: "Branch Chat" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });

  it("lists conversations from the API", async () => {
    const fetchMock = mockFetch(
      jsonResponse([
        { id: "c1", title: "First", created_at: "2026-10-28T00:00:00Z", root_message_id: "m1" },
        { id: "c2", title: "Second", created_at: "2026-10-28T00:01:00Z", root_message_id: "m2" },
      ]),
    );
    render(<App />);
    const list = await screen.findByRole("list", { name: "Conversations" });
    expect(list).toHaveTextContent("First");
    expect(list).toHaveTextContent("Second");
    expect(fetchMock).toHaveBeenCalledWith("/api/conversations", expect.anything());
  });

  it("shows an empty state", async () => {
    mockFetch(jsonResponse([]));
    render(<App />);
    expect(await screen.findByText("No conversations yet.")).toBeInTheDocument();
  });

  it("shows an error when the API fails", async () => {
    mockFetch(jsonResponse({ detail: "boom" }, 500));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});

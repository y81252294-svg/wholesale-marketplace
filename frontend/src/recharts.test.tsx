import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import * as Recharts from "recharts";

describe("forecast chart", () => {
  it("renders a forecast chart container and exposes its series components", () => {
    const markup = renderToStaticMarkup(
      createElement(Recharts.AreaChart, {
        width: 400,
        height: 240,
        data: [{ target_period: "2026-01-05", predicted_qty: 12 }],
      }),
    );

    expect(markup).toContain("recharts-wrapper");
    expect(Recharts.Area).toBeDefined();
    expect(Recharts.CartesianGrid).toBeDefined();
    expect(Recharts.Tooltip).toBeDefined();
    expect(Recharts.XAxis).toBeDefined();
    expect(Recharts.YAxis).toBeDefined();
  });
});

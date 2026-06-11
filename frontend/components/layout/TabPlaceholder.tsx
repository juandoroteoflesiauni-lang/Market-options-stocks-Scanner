"use client";
import {
  StaggerContainer,
  StaggerCard,
  staggerContainerProps,
  staggerCardProps,
} from "./TabTransition";

interface Feature {
  label: string;
  desc: string;
}

interface Props {
  index: string;
  title: string;
  subtitle: string;
  accentColor?: string;
  features: Feature[];
  rightPanel?: Feature[];
}

function FeatureItem({ label, desc }: Feature) {
  return (
    <div
      className="flex items-start gap-3 px-4 py-3 rounded"
      style={{
        background: "rgba(255,255,255,0.02)",
        border: "1px solid rgba(255,255,255,0.05)",
      }}
    >
      <span
        className="mt-0.5 w-1.5 h-1.5 rounded-full shrink-0"
        style={{ background: "#00C3FF", marginTop: 5 }}
      />
      <div>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "#E8EDF5",
            letterSpacing: "0.05em",
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 11,
            color: "#4A5568",
            marginTop: 2,
          }}
        >
          {desc}
        </div>
      </div>
    </div>
  );
}

export function TabPlaceholder({
  index,
  title,
  subtitle,
  accentColor = "#00C3FF",
  features,
  rightPanel,
}: Props) {
  return (
    <StaggerContainer
      {...staggerContainerProps}
      className="h-full flex flex-col gap-4"
    >
      {/* Header row */}
      <StaggerCard {...staggerCardProps}>
        <div
          className="flex items-center justify-between px-6 py-4 rounded-xl"
          style={{
            background: "var(--bg-panel)",
            border: "1px solid rgba(255,255,255,0.06)",
          }}
        >
          <div className="flex items-center gap-4">
            <div
              className="flex items-center justify-center w-10 h-10 rounded-lg"
              style={{
                background: `${accentColor}18`,
                border: `1px solid ${accentColor}44`,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 13,
                  color: accentColor,
                  fontWeight: 700,
                }}
              >
                {index}
              </span>
            </div>
            <div>
              <div
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 20,
                  color: "#E8EDF5",
                  fontWeight: 600,
                }}
              >
                {title}
              </div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "#8B9AAF",
                  marginTop: 2,
                  letterSpacing: "0.05em",
                }}
              >
                {subtitle}
              </div>
            </div>
          </div>

          <div
            className="px-3 py-1.5 rounded"
            style={{
              background: "rgba(0,195,255,0.08)",
              border: "1px solid rgba(0,195,255,0.20)",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#00C3FF",
                letterSpacing: "0.12em",
              }}
            >
              PHASE A — PLACEHOLDER
            </span>
          </div>
        </div>
      </StaggerCard>

      {/* Main content area */}
      <div className="flex-1 flex gap-4 min-h-0">
        {/* Features list */}
        <StaggerCard
          {...staggerCardProps}
          style={{ flex: rightPanel ? "0 0 60%" : "1" }}
        >
          <div
            className="h-full rounded-xl p-5 flex flex-col gap-3"
            style={{
              background: "var(--bg-panel)",
              border: "1px solid rgba(255,255,255,0.06)",
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "#4A5568",
                letterSpacing: "0.12em",
                marginBottom: 4,
              }}
            >
              COMPONENTS TO BE BUILT
            </div>
            <div className="grid gap-2">
              {features.map((f, i) => (
                <FeatureItem key={i} {...f} />
              ))}
            </div>
          </div>
        </StaggerCard>

        {/* Right panel if provided */}
        {rightPanel && (
          <StaggerCard {...staggerCardProps} className="flex-1">
            <div
              className="h-full rounded-xl p-5 flex flex-col gap-3"
              style={{
                background: "var(--bg-panel)",
                border: "1px solid rgba(255,255,255,0.06)",
              }}
            >
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "#4A5568",
                  letterSpacing: "0.12em",
                  marginBottom: 4,
                }}
              >
                ADDITIONAL PANELS
              </div>
              <div className="grid gap-2">
                {rightPanel.map((f, i) => (
                  <FeatureItem key={i} {...f} />
                ))}
              </div>
            </div>
          </StaggerCard>
        )}
      </div>

      {/* Bottom status bar */}
      <StaggerCard {...staggerCardProps}>
        <div
          className="flex items-center gap-3 px-4 py-2.5 rounded-lg"
          style={{
            background: "rgba(0,195,255,0.04)",
            border: "1px solid rgba(0,195,255,0.12)",
          }}
        >
          <span
            className="w-2 h-2 rounded-full"
            style={{
              background: "#FFB800",
              animation: "strobe-red 2s ease-in-out infinite",
            }}
          />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "#8B9AAF",
              letterSpacing: "0.05em",
            }}
          >
            Awaiting Phase B implementation — shared component library builds
            next
          </span>
        </div>
      </StaggerCard>
    </StaggerContainer>
  );
}

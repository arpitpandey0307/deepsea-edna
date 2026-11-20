import { getConfidenceTier } from '@/lib/utils';

interface ConfidenceGaugeProps {
  confidence: number; // 0..1 or 0..100
  size?: number;
  label?: string;
}

export function ConfidenceGauge({
  confidence,
  size = 156,
  label = 'Confidence',
}: ConfidenceGaugeProps) {
  const normalized = confidence > 1 ? confidence / 100 : confidence;
  const clamped = Math.max(0, Math.min(1, normalized));
  const pct = clamped * 100;
  const tier = getConfidenceTier(clamped);

  const strokeWidth = 11;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped);

  const strokeColor =
    tier.tone === 'emerald'
      ? '#10B981'
      : tier.tone === 'cyan'
      ? '#22D3EE'
      : tier.tone === 'amber'
      ? '#F59E0B'
      : '#F43F5E';

  return (
    <div
      className="relative inline-flex items-center justify-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${label}: ${pct.toFixed(1)}% (${tier.tier})`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-slate-200 dark:text-slate-800"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={strokeColor}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: 'stroke-dashoffset 450ms cubic-bezier(0.16, 1, 0.3, 1)' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        <span className="text-2xl font-semibold font-mono tabular-nums text-slate-900 dark:text-slate-100">
          {pct.toFixed(1)}%
        </span>
        <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
          {label}
        </span>
      </div>
    </div>
  );
}

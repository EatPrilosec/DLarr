import React from 'react';
import { CheckCircle2, AlertTriangle, HelpCircle, XCircle, Sparkles } from 'lucide-react';

interface StatusBadgeProps {
  status: string;
  confidence?: number;
  showIcon?: boolean;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, confidence, showIcon = true }) => {
  let badgeStyle = "bg-slate-800 text-slate-300 border-slate-700";
  let icon = <HelpCircle className="w-3.5 h-3.5" />;
  let label = status;

  switch (status?.toUpperCase()) {
    case 'EXACT_MATCH':
    case 'PASSED':
      badgeStyle = "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
      icon = <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />;
      label = status === 'PASSED' ? 'Audit Passed' : 'Exact Match';
      break;

    case 'AI_MATCHED':
      badgeStyle = "bg-indigo-500/10 text-indigo-400 border-indigo-500/30";
      icon = <Sparkles className="w-3.5 h-3.5 text-indigo-400" />;
      label = confidence ? `AI Matched (${Math.round(confidence * 100)}%)` : 'AI Matched';
      break;

    case 'FLAGGED_MISMATCH':
    case 'HAS_WARNINGS':
      badgeStyle = "bg-amber-500/10 text-amber-400 border-amber-500/30";
      icon = <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />;
      label = status === 'HAS_WARNINGS' ? 'Audit Warnings' : 'Mismatch Flagged';
      break;

    case 'FAILED':
    case 'NO_MATCH':
      badgeStyle = "bg-rose-500/10 text-rose-400 border-rose-500/30";
      icon = <XCircle className="w-3.5 h-3.5 text-rose-400" />;
      label = 'No Match';
      break;

    case 'PENDING':
    case 'NOT_AUDITED':
      badgeStyle = "bg-slate-500/10 text-slate-400 border-slate-500/30";
      icon = <HelpCircle className="w-3.5 h-3.5 text-slate-400" />;
      label = status === 'NOT_AUDITED' ? 'Not Audited' : 'Pending';
      break;
  }

  return (
    <span className={`inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${badgeStyle}`}>
      {showIcon && icon}
      <span>{label}</span>
    </span>
  );
};

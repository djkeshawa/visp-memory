import { ReactNode } from 'react';

interface StatsCardProps {
    title: string;
    value: number | string;
    icon?: ReactNode;
    change?: string;
    changeType?: 'positive' | 'negative' | 'neutral';
}

export function StatsCard({ title, value, icon, change, changeType = 'neutral' }: StatsCardProps) {
    return (
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100 hover:shadow-md transition-shadow duration-200">
            <div className="flex items-start justify-between">
                <div>
                    <p className="text-sm font-medium text-slate-500 mb-1">{title}</p>
                    <p className="text-3xl font-semibold text-slate-900 tracking-tight">{value}</p>
                    {change && (
                        <p className={`text-xs mt-2 font-medium ${changeType === 'positive' ? 'text-emerald-600' :
                                changeType === 'negative' ? 'text-rose-600' :
                                    'text-slate-400'
                            }`}>
                            {change}
                        </p>
                    )}
                </div>
                {icon && (
                    <div className="w-10 h-10 rounded-xl bg-slate-50 flex items-center justify-center text-slate-400">
                        {icon}
                    </div>
                )}
            </div>
        </div>
    );
}

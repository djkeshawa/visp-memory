'use client';

import { Memory } from '@/lib/types';
import { Clock, Brain, Zap, FileText } from 'lucide-react';

interface RecentActivityProps {
    memories: Memory[];
}

export function RecentActivity({ memories }: RecentActivityProps) {
    const getIcon = (layer: string) => {
        const iconClass = "flex-shrink-0";
        switch (layer) {
            case 'episodic': return <Clock size={16} className={`${iconClass} text-blue-500`} />;
            case 'semantic': return <Brain size={16} className={`${iconClass} text-violet-500`} />;
            case 'intent': return <Zap size={16} className={`${iconClass} text-amber-500`} />;
            default: return <FileText size={16} className={`${iconClass} text-slate-400`} />;
        }
    };

    const getLayerBadge = (layer: string) => {
        const base = "text-xs font-medium px-2 py-0.5 rounded-full";
        switch (layer) {
            case 'episodic': return `${base} bg-blue-50 text-blue-600`;
            case 'semantic': return `${base} bg-violet-50 text-violet-600`;
            case 'intent': return `${base} bg-amber-50 text-amber-600`;
            default: return `${base} bg-slate-100 text-slate-600`;
        }
    };

    return (
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100">
            <div className="p-5 border-b border-slate-100">
                <h3 className="text-base font-semibold text-slate-900">Recent Activity</h3>
                <p className="text-sm text-slate-500 mt-0.5">Latest memory recordings</p>
            </div>

            <div className="divide-y divide-slate-100">
                {!memories?.length ? (
                    <div className="p-8 text-center">
                        <div className="w-12 h-12 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-3">
                            <Clock size={24} className="text-slate-400" />
                        </div>
                        <p className="text-sm text-slate-500">No recent activity</p>
                    </div>
                ) : (
                    memories.slice(0, 8).map((mem) => (
                        <div key={mem.id} className="p-4 hover:bg-slate-50 transition-colors">
                            <div className="flex gap-3">
                                <div className="mt-0.5">
                                    {getIcon(mem.layer)}
                                </div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm text-slate-700 line-clamp-2 leading-relaxed">
                                        {mem.content}
                                    </p>
                                    <div className="flex items-center gap-2 mt-2">
                                        <span className={getLayerBadge(mem.layer)}>
                                            {mem.layer}
                                        </span>
                                        {mem.category && (
                                            <span className="text-xs text-slate-400">
                                                {mem.category}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}

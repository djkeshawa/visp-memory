'use client';

import { useIntents } from "@/lib/api";
import { CheckCircle2, Circle, Plus } from "lucide-react";
import { AppLayout } from "@/components/layout/AppLayout";

export default function IntentsPage() {
    const { intents, isLoading } = useIntents();

    const active = intents?.filter(i => i.status === 'active') || [];
    const completed = intents?.filter(i => i.status === 'completed') || [];

    return (
        <AppLayout>
            <div className="max-w-4xl mx-auto">
                {/* Header */}
                <div className="flex items-center justify-between mb-8">
                    <div>
                        <h1 className="text-2xl font-semibold text-slate-900">Intents</h1>
                        <p className="text-slate-500 mt-1">Track your objectives and goals</p>
                    </div>
                    <button className="flex items-center gap-2 px-4 py-2.5 bg-blue-500 hover:bg-blue-600 text-white text-sm font-medium rounded-xl transition-colors">
                        <Plus size={16} />
                        New Intent
                    </button>
                </div>

                {isLoading ? (
                    <div className="flex items-center justify-center h-64">
                        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {/* Active Column */}
                        <div>
                            <div className="flex items-center gap-2 mb-4">
                                <h2 className="text-sm font-semibold text-slate-900">Active</h2>
                                <span className="px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-700 rounded-full">
                                    {active.length}
                                </span>
                            </div>

                            <div className="space-y-3">
                                {active.length === 0 ? (
                                    <div className="bg-white rounded-2xl border border-slate-200 border-dashed p-6 text-center">
                                        <Circle size={24} className="text-slate-300 mx-auto mb-2" />
                                        <p className="text-sm text-slate-500">No active intents</p>
                                    </div>
                                ) : (
                                    active.map(intent => (
                                        <div key={intent.id} className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm hover:shadow-md transition-shadow">
                                            <div className="flex items-start gap-3">
                                                <div className="w-5 h-5 rounded-full border-2 border-amber-400 flex-shrink-0 mt-0.5" />
                                                <div className="flex-1">
                                                    <p className="text-sm font-medium text-slate-900">{intent.description}</p>
                                                    <div className="flex items-center gap-2 mt-2">
                                                        <span className="text-xs text-slate-500">Priority {intent.priority}</span>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>

                        {/* Completed Column */}
                        <div>
                            <div className="flex items-center gap-2 mb-4">
                                <h2 className="text-sm font-semibold text-slate-900">Completed</h2>
                                <span className="px-2 py-0.5 text-xs font-medium bg-emerald-100 text-emerald-700 rounded-full">
                                    {completed.length}
                                </span>
                            </div>

                            <div className="space-y-3">
                                {completed.length === 0 ? (
                                    <div className="bg-white rounded-2xl border border-slate-200 border-dashed p-6 text-center">
                                        <CheckCircle2 size={24} className="text-slate-300 mx-auto mb-2" />
                                        <p className="text-sm text-slate-500">No completed intents</p>
                                    </div>
                                ) : (
                                    completed.map(intent => (
                                        <div key={intent.id} className="bg-white rounded-2xl border border-slate-200 p-4 opacity-75">
                                            <div className="flex items-start gap-3">
                                                <CheckCircle2 size={20} className="text-emerald-500 flex-shrink-0 mt-0.5" />
                                                <div className="flex-1">
                                                    <p className="text-sm text-slate-500 line-through">{intent.description}</p>
                                                </div>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </AppLayout>
    );
}

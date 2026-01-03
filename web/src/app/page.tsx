'use client';

import { useStats, useMemories } from "@/lib/api";
import { StatsCard } from "@/components/dashboard/StatsCard";
import { RecentActivity } from "@/components/dashboard/RecentActivity";
import { Brain, Database, Target, Share2, Plus, Search } from "lucide-react";
import { AppLayout } from "@/components/layout/AppLayout";

export default function Home() {
  const { stats, isLoading: statsLoading } = useStats();
  const { memories, isLoading: memoriesLoading } = useMemories();

  return (
    <AppLayout>
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-slate-900">
            Welcome back
          </h1>
          <p className="text-slate-500 mt-1">
            Here's what's happening with your memory system.
          </p>
        </div>

        {(statsLoading || memoriesLoading) ? (
          <div className="flex items-center justify-center h-64">
            <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <>
            {/* Stats Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
              <StatsCard
                title="Total Memories"
                value={stats?.total_memories || 0}
                icon={<Database size={20} />}
                change="All time"
              />
              <StatsCard
                title="Active Intents"
                value={stats?.active_intents || 0}
                icon={<Target size={20} />}
                change="In progress"
                changeType="neutral"
              />
              <StatsCard
                title="Knowledge Nodes"
                value={stats?.memories_by_layer?.semantic || 0}
                icon={<Brain size={20} />}
                change="Semantic layer"
              />
              <StatsCard
                title="Connections"
                value={stats?.total_relationships || 0}
                icon={<Share2 size={20} />}
                change="Graph edges"
              />
            </div>

            {/* Main Content */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Activity Feed */}
              <div className="lg:col-span-2">
                <RecentActivity memories={memories || []} />
              </div>

              {/* Side Panel */}
              <div className="space-y-4">
                {/* Quick Actions */}
                <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5">
                  <h3 className="text-base font-semibold text-slate-900 mb-4">Quick Actions</h3>
                  <div className="space-y-2">
                    <button className="w-full flex items-center justify-center gap-2 py-2.5 px-4 bg-blue-500 hover:bg-blue-600 text-white text-sm font-medium rounded-xl transition-colors">
                      <Plus size={16} />
                      New Memory
                    </button>
                    <button className="w-full flex items-center justify-center gap-2 py-2.5 px-4 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-xl transition-colors">
                      <Search size={16} />
                      Search
                    </button>
                  </div>
                </div>

                {/* System Status */}
                <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5">
                  <h3 className="text-base font-semibold text-slate-900 mb-4">System Status</h3>
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-slate-600">API Server</span>
                      <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-600">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                        Online
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-slate-600">Vector Database</span>
                      <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-600">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                        Ready
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-slate-600">Embeddings</span>
                      <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-600">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                        Active
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </AppLayout>
  );
}

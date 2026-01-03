'use client';

import { useMemories } from "@/lib/api";
import { AppLayout } from "@/components/layout/AppLayout";
import React, { useMemo } from 'react';
import ReactFlow, {
    Background,
    Controls,
    Node,
    Edge
} from 'reactflow';
import 'reactflow/dist/style.css';

export default function GraphPage() {
    const { memories, isLoading } = useMemories();

    const { initialNodes, initialEdges } = useMemo(() => {
        if (!memories) return { initialNodes: [], initialEdges: [] };

        const nodes: Node[] = memories.map((mem, index) => ({
            id: mem.id,
            position: { x: (index % 5) * 220 + 50, y: Math.floor(index / 5) * 120 + 50 },
            data: { label: mem.content.substring(0, 35) + '...' },
            style: {
                background: mem.layer === 'episodic' ? '#3b82f6' :
                    mem.layer === 'semantic' ? '#8b5cf6' : '#f59e0b',
                color: 'white',
                border: 'none',
                borderRadius: '12px',
                padding: '12px 16px',
                fontSize: '13px',
                fontWeight: 500,
                width: 180,
                boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            }
        }));

        const edges: Edge[] = [];
        return { initialNodes: nodes, initialEdges: edges };
    }, [memories]);

    return (
        <AppLayout>
            <div className="max-w-6xl mx-auto">
                {/* Header */}
                <div className="mb-6">
                    <h1 className="text-2xl font-semibold text-slate-900">Memory Graph</h1>
                    <p className="text-slate-500 mt-1">Visualize connections between your memories</p>
                </div>

                {/* Legend */}
                <div className="flex items-center gap-6 mb-4">
                    <div className="flex items-center gap-2">
                        <span className="w-3 h-3 rounded-full bg-blue-500" />
                        <span className="text-sm text-slate-600">Episodic</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="w-3 h-3 rounded-full bg-violet-500" />
                        <span className="text-sm text-slate-600">Semantic</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="w-3 h-3 rounded-full bg-amber-500" />
                        <span className="text-sm text-slate-600">Intent</span>
                    </div>
                </div>

                {/* Graph Container */}
                {isLoading ? (
                    <div className="flex items-center justify-center h-[500px] bg-white rounded-2xl border border-slate-200">
                        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    </div>
                ) : (
                    <div className="h-[500px] bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                        <ReactFlow
                            nodes={initialNodes}
                            edges={initialEdges}
                            fitView
                        >
                            <Background color="#e2e8f0" gap={20} />
                            <Controls />
                        </ReactFlow>
                    </div>
                )}
            </div>
        </AppLayout>
    );
}

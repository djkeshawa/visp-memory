'use client';

import { useState } from 'react';
import { searchMemories } from '@/lib/api';
import { Search, Sparkles } from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { RecentActivity } from '@/components/dashboard/RecentActivity';

export default function RecallPage() {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<any[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const [hasSearched, setHasSearched] = useState(false);

    const handleSearch = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!query.trim()) return;

        setIsSearching(true);
        setHasSearched(true);
        try {
            const res = await searchMemories(query);
            setResults(res);
        } catch (error) {
            console.error(error);
        } finally {
            setIsSearching(false);
        }
    };

    return (
        <AppLayout>
            <div className="max-w-2xl mx-auto">
                {/* Header */}
                <div className="text-center mb-8">
                    <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-2xl flex items-center justify-center mx-auto mb-4">
                        <Sparkles size={24} className="text-white" />
                    </div>
                    <h1 className="text-2xl font-semibold text-slate-900">Recall</h1>
                    <p className="text-slate-500 mt-2">Search your memories using natural language</p>
                </div>

                {/* Search Form */}
                <form onSubmit={handleSearch} className="mb-8">
                    <div className="relative">
                        <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" size={20} />
                        <input
                            type="text"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="What did I decide about authentication?"
                            className="w-full h-14 pl-12 pr-28 bg-white border border-slate-200 rounded-2xl text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent shadow-sm transition-all"
                        />
                        <button
                            type="submit"
                            disabled={isSearching}
                            className="absolute right-2 top-2 h-10 px-5 bg-blue-500 hover:bg-blue-600 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition-colors"
                        >
                            {isSearching ? 'Searching...' : 'Search'}
                        </button>
                    </div>
                </form>

                {/* Results */}
                {hasSearched && (
                    <div>
                        {results.length > 0 && (
                            <p className="text-sm text-slate-500 mb-4">
                                Found {results.length} result{results.length !== 1 ? 's' : ''}
                            </p>
                        )}

                        {results.length > 0 ? (
                            <RecentActivity memories={results} />
                        ) : (
                            !isSearching && (
                                <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center shadow-sm">
                                    <p className="text-slate-500">No memories found matching your query.</p>
                                </div>
                            )
                        )}
                    </div>
                )}

                {/* Empty State */}
                {!hasSearched && (
                    <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center shadow-sm">
                        <p className="text-slate-500">Enter a query to search your second brain.</p>
                        <div className="flex flex-wrap justify-center gap-2 mt-4">
                            {['authentication', 'database schema', 'API design'].map((suggestion) => (
                                <button
                                    key={suggestion}
                                    onClick={() => setQuery(suggestion)}
                                    className="px-3 py-1.5 text-xs font-medium text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-full transition-colors"
                                >
                                    {suggestion}
                                </button>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </AppLayout>
    );
}

'use client';

import Link from 'next/link';
import { LayoutDashboard, Brain, Search, Target, Sparkles } from 'lucide-react';
import { usePathname } from 'next/navigation';

const navItems = [
    { name: 'Dashboard', href: '/', icon: LayoutDashboard },
    { name: 'Memory Graph', href: '/graph', icon: Brain },
    { name: 'Recall', href: '/recall', icon: Search },
    { name: 'Intents', href: '/intents', icon: Target },
];

export function Sidebar() {
    const pathname = usePathname();

    return (
        <aside className="w-60 min-w-60 h-screen bg-white border-r border-slate-200 flex flex-col sticky top-0">
            {/* Logo */}
            <div className="p-5 border-b border-slate-100">
                <div className="flex items-center gap-2">
                    <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-lg flex items-center justify-center">
                        <Sparkles size={18} className="text-white" />
                    </div>
                    <div>
                        <h1 className="text-base font-semibold text-slate-900">LLM Memory</h1>
                        <p className="text-xs text-slate-400">Central Context</p>
                    </div>
                </div>
            </div>

            {/* Navigation */}
            <nav className="flex-1 p-3">
                <div className="space-y-1">
                    {navItems.map((item) => {
                        const isActive = pathname === item.href;
                        return (
                            <Link
                                key={item.name}
                                href={item.href}
                                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${isActive
                                        ? 'bg-blue-50 text-blue-600'
                                        : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                                    }`}
                            >
                                <item.icon size={18} strokeWidth={isActive ? 2 : 1.5} />
                                <span>{item.name}</span>
                            </Link>
                        );
                    })}
                </div>
            </nav>

            {/* Footer */}
            <div className="p-4 border-t border-slate-100">
                <div className="flex items-center gap-2 text-xs">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    <span className="text-slate-400">Connected</span>
                </div>
            </div>
        </aside>
    );
}

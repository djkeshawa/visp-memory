
// web/src/components/ui/FormattedDate.tsx
'use client';
import { useEffect, useState } from 'react';

export function FormattedDate({ date }: { date: string | Date }) {
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted) return <span className="text-xs text-gray-500">...</span>;

    return <>{new Date(date).toLocaleString()}</>;
}

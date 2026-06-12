export default function Layout({ children }) {
  return (
    <div className="min-h-screen bg-[#0F0F0F] text-white">
      <style>{`
        :root {
          --gradient-primary: linear-gradient(135deg, #EC4899 0%, #8B5CF6 50%, #3B82F6 100%);
          --gradient-subtle: linear-gradient(135deg, rgba(236, 72, 153, 0.1) 0%, rgba(139, 92, 246, 0.1) 50%, rgba(59, 130, 246, 0.1) 100%);
        }
        
        .gradient-text {
          background: var(--gradient-primary);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
        }
        
        .gradient-border {
          position: relative;
        }
        
        .gradient-border::before {
          content: '';
          position: absolute;
          inset: 0;
          padding: 1px;
          border-radius: inherit;
          background: var(--gradient-primary);
          -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
          mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
          -webkit-mask-composite: xor;
          mask-composite: exclude;
        }
        
        .glass-card {
          background: rgba(255, 255, 255, 0.03);
          backdrop-filter: blur(10px);
          border: 1px solid rgba(255, 255, 255, 0.05);
        }
        
        /* Chat: thinking shimmer label, status word swap, citation pill pop-in */
        @keyframes cm-shimmer {
          from { background-position: 200% 0; }
          to { background-position: -120% 0; }
        }
        @keyframes cm-word-in {
          from { opacity: 0; transform: translateY(5px); }
          to { opacity: 1; transform: none; }
        }
        .cm-shimmer {
          display: inline-block;
          background: linear-gradient(90deg,
            rgba(229, 231, 235, 0.35) 0%,
            #DDD6FE 45%, #fff 50%, #DDD6FE 55%,
            rgba(229, 231, 235, 0.35) 100%);
          background-size: 220% 100%;
          -webkit-background-clip: text;
          background-clip: text;
          color: transparent;
        }
        .cm-shimmer.cm-word-in {
          animation: cm-shimmer 1.8s linear infinite,
                     cm-word-in 0.32s cubic-bezier(0.22, 1, 0.36, 1);
        }
        @keyframes cm-pop-in {
          from { opacity: 0; transform: scale(0.4); }
        }
        .cm-pop-in {
          animation: cm-pop-in 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) both;
        }

        .neon-glow {
          box-shadow: 0 0 20px rgba(139, 92, 246, 0.15), 0 0 40px rgba(236, 72, 153, 0.1);
        }
        
        .btn-gradient {
          background: var(--gradient-primary);
          transition: all 0.3s ease;
        }
        
        .btn-gradient:hover {
          transform: scale(1.02);
          box-shadow: 0 0 30px rgba(139, 92, 246, 0.4), 0 0 60px rgba(236, 72, 153, 0.2);
        }
        
        * {
          scrollbar-width: thin;
          scrollbar-color: rgba(139, 92, 246, 0.3) transparent;
        }
        
        *::-webkit-scrollbar {
          width: 6px;
        }
        
        *::-webkit-scrollbar-track {
          background: transparent;
        }
        
        *::-webkit-scrollbar-thumb {
          background: rgba(139, 92, 246, 0.3);
          border-radius: 3px;
        }
      `}</style>
      {children}
    </div>
  );
}

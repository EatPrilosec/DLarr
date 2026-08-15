import React, { useState } from 'react';
import { Navbar } from './components/Navbar';
import { Dashboard } from './pages/Dashboard';
import { ShowDetail } from './pages/ShowDetail';
import { Settings } from './pages/Settings';
import { Activity } from './pages/Activity';
import { ImportModal } from './components/ImportModal';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<'dashboard' | 'settings' | 'activity'>('dashboard');
  const [selectedShowId, setSelectedShowId] = useState<number | null>(null);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);

  const handleSelectShow = (id: number) => {
    setSelectedShowId(id);
  };

  const handleBackToLibrary = () => {
    setSelectedShowId(null);
    setCurrentTab('dashboard');
  };

  const handleShowImported = (jobId: number) => {
    setActiveJobId(jobId);
    setCurrentTab('activity');
  };

  return (
    <div className="min-h-screen bg-dark-900 text-slate-100 flex flex-col selection:bg-indigo-500 selection:text-white">
      <Navbar
        currentTab={selectedShowId ? 'dashboard' : currentTab}
        setCurrentTab={(tab: any) => {
          setSelectedShowId(null);
          setCurrentTab(tab);
        }}
        onOpenImport={() => setIsImportModalOpen(true)}
      />

      <main className="flex-1 pb-16">
        {selectedShowId ? (
          <ShowDetail
            showId={selectedShowId}
            onBack={handleBackToLibrary}
            onJobStarted={(jobId) => {
              setActiveJobId(jobId);
              setCurrentTab('activity');
              setSelectedShowId(null);
            }}
          />
        ) : (
          <>
            {currentTab === 'dashboard' && (
              <Dashboard
                onSelectShow={handleSelectShow}
                onOpenImport={() => setIsImportModalOpen(true)}
              />
            )}
            {currentTab === 'settings' && <Settings />}
            {currentTab === 'activity' && <Activity activeJobId={activeJobId} />}
          </>
        )}
      </main>

      <ImportModal
        isOpen={isImportModalOpen}
        onClose={() => setIsImportModalOpen(false)}
        onShowImported={handleShowImported}
      />
    </div>
  );
};

export default App;

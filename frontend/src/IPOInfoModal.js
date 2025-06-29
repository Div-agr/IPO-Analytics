import React from 'react';

const IPOInfoModal = ({ show, onClose, date, ipos }) => {
  if (!show) return null;

  return (
    <div style={modalStyles}>
      <div style={modalContentStyles}>
        <div style={headerStyles}>
          <h2 style={titleStyles}>📈 IPOs on {date}</h2>
          <button onClick={onClose} style={closeButtonStyles}>×</button>
        </div>

        {ipos.length === 0 ? (
          <p style={noDataStyles}>No IPOs found for this date.</p>
        ) : (
          <ul style={listStyles}>
            {ipos.map((ipo, index) => (
              <li key={index} style={itemStyles}>
                <strong>{ipo.IPO}</strong>
                <div style={probabilityStyles}>
                  Success Probability: <span>{ipo.Apply_Probability}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

// Styles
const modalStyles = {
  position: 'fixed',
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  backgroundColor: 'rgba(0, 0, 0, 0.6)',
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'center',
  zIndex: 1000,
};

const modalContentStyles = {
  backgroundColor: '#1e1f22',
  padding: '1.5rem',
  borderRadius: '12px',
  width: '90%',
  maxWidth: '450px',
  color: '#f0f0f0',
  boxShadow: '0 8px 24px rgba(0, 0, 0, 0.8)',
  animation: 'fadeIn 0.3s ease',
  fontFamily: 'Segoe UI, sans-serif',
};

const headerStyles = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: '1rem',
};

const titleStyles = {
  margin: 0,
  fontSize: '1.4rem',
  fontWeight: '600',
};

const closeButtonStyles = {
  background: 'none',
  color: '#f0f0f0',
  border: 'none',
  fontSize: '1.5rem',
  cursor: 'pointer',
  transition: 'color 0.3s ease',
};

const noDataStyles = {
  color: '#aaa',
  fontSize: '1rem',
};

const listStyles = {
  listStyle: 'none',
  padding: 0,
  maxHeight: '300px',
  overflowY: 'auto',
};

const itemStyles = {
  backgroundColor: '#2c2f33',
  padding: '0.75rem',
  borderRadius: '8px',
  marginBottom: '10px',
  boxShadow: 'inset 0 0 10px rgba(0,0,0,0.3)',
};

const probabilityStyles = {
  fontSize: '0.95rem',
  color: '#ccc',
  marginTop: '4px',
};

export default IPOInfoModal;

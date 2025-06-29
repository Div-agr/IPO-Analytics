import React, { useState, useEffect } from 'react';
import { Calendar, momentLocalizer } from 'react-big-calendar';
import moment from 'moment';
import axios from 'axios';
import IPOInfoModal from './IPOInfoModal';
import 'react-big-calendar/lib/css/react-big-calendar.css';
import './index.css'; // Your dark theme customizations

const localizer = momentLocalizer(moment);

const CalendarComponent = () => {
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [calendarDate, setCalendarDate] = useState(new Date());
  const [ipoData, setIpoData] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [events, setEvents] = useState([]);
  const [email, setEmail] = useState('');
  const [emailError, setEmailError] = useState('');

  const colors = ['#FF8A65', '#4DB6AC', '#BA68C8', '#7986CB', '#FFD54F', '#81C784', '#F06292'];

  const fetchAllIPOsForMonth = async () => {
    const startOfMonth = '2021-01-01';
    const endOfMonth = moment().endOf('month').format('YYYY-MM-DD');
    console.log(endOfMonth);

    try {
      const response = await axios.get(
        `http://127.0.0.1:5000/api/ipo_data_range?start=${startOfMonth}&end=${endOfMonth}`
      );
      const ipoList = response.data.ipos || [];

      const ipoEvents = ipoList.map((ipo, index) => ({
        title: ipo.IPO || `IPO ${index + 1}`,
        start: new Date(ipo['Apply Date'] + 'T09:00:00'),
        end: new Date(ipo['Apply Date'] + 'T09:30:00'),
        allDay: false,
        ipoDetails: ipo,
        color: colors[index % colors.length],
      }));

      setEvents(ipoEvents);
    } catch (error) {
      console.error('Error fetching all IPOs:', error);
    }
  };

  const handleEventClick = (event) => {
    const clickedDate = moment(event.start).format('YYYY-MM-DD');
    setSelectedDate(new Date(clickedDate));

    const iposOnDate = events
      .filter((e) => moment(e.start).format('YYYY-MM-DD') === clickedDate)
      .map((e) => e.ipoDetails);

    setIpoData(iposOnDate);
    if (iposOnDate.length > 0) {
      setShowModal(true);
    }
  };

  const handleDateClick = (slotInfo) => {
    const clickedDate = moment(slotInfo.start).format('YYYY-MM-DD');
    setSelectedDate(new Date(clickedDate));

    const iposOnDate = events
      .filter((event) => moment(event.start).format('YYYY-MM-DD') === clickedDate)
      .map((event) => event.ipoDetails);

    setIpoData(iposOnDate);
    if (iposOnDate.length > 0) {
      setShowModal(true);
    }
  };

  // 🟡 Manual Date Picker Handler
  const handleManualDateChange = (e) => {
    const date = new Date(e.target.value);
    if (!isNaN(date)) {
      setCalendarDate(date);
    }
  };

  const isValidEmail = (email) =>
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);

  const handleEmailChange = (e) => {
    setEmail(e.target.value);
    setEmailError('');
  };

  const handleSubscribe = async () => {
    if (!isValidEmail(email)) {
      setEmailError('Please enter a valid email address.');
      return;
    }

    try {
      const response = await axios.post('http://localhost:5000/api/subscribe', { email });
      alert('Subscribed successfully!');
      setEmail('');
    } catch (error) {
      if (error.response && error.response.status === 409) {
        setEmailError('This email is already subscribed.');
      } else {
        console.error('Subscription failed:', error);
        setEmailError('Subscription failed. Try again later.');
      }
    }
  };

  useEffect(() => {
    fetchAllIPOsForMonth();
  }, []);

  return (
    <div className="savvycal-calendar-container">
      <h1 style={{ color: '#fff', marginBottom: '10px', textAlign: 'center' }}>
        Upcoming IPOs Calendar
      </h1>

      <div
        style={{
          position: 'absolute',
          top: '20px',
          right: '30px',
          backgroundColor: '#2b2b2b',
          padding: '20px',
          borderRadius: '8px',
          boxShadow: '0 0 10px rgba(0, 0, 0, 0.3)',
          zIndex: 1000,
        }}
      >
        <h3 style={{ color: '#fff', marginBottom: '10px' }}>Subscribe for IPO Alerts</h3>
        <input
          type="email"
          value={email}
          onChange={handleEmailChange}
          placeholder="Enter your email"
          style={{
            padding: '10px',
            width: '200px',
            borderRadius: '5px',
            border: '1px solid #ccc',
            marginBottom: '10px',
            marginRight: '5px',
          }}
        />
        <button
          onClick={handleSubscribe}
          disabled={!isValidEmail(email)}
          style={{
            padding: '10px 20px',
            borderRadius: '5px',
            backgroundColor: isValidEmail(email) ? 'red' : '#888',
            color: 'white',
            border: 'none',
            cursor: isValidEmail(email) ? 'pointer' : 'not-allowed',
          }}
        >
          Subscribe
        </button>
        {emailError && (
          <p style={{ color: 'red', marginTop: '10px', maxWidth: '250px' }}>{emailError}</p>
        )}
      </div>

      {/* 🔘 Manual Date Picker */}
      <div style={{ textAlign: 'center', marginBottom: '20px' }}>
        <input
          type="date"
          onChange={handleManualDateChange}
          value={moment(calendarDate).format('YYYY-MM-DD')}
          style={{
            padding: '8px',
            borderRadius: '6px',
            border: '1px solid #ccc',
            background: '#1e1e1e',
            color: 'white',
          }}
        />
      </div>

      <Calendar
        localizer={localizer}
        events={events}
        startAccessor="start"
        endAccessor="end"
        defaultView="month"
        views={['month']}
        style={{ height: '80vh' }}
        onSelectEvent={handleEventClick}
        onSelectSlot={handleDateClick}
        selectable
        date={calendarDate}
        onNavigate={(newDate) => setCalendarDate(newDate)}
        className="savvycal-calendar"
        eventPropGetter={(event) => {
          const backgroundColor = event.color || '#2196F3';
          return {
            style: {
              backgroundColor,
              borderRadius: '5px',
              color: 'white',
              border: 'none',
              paddingLeft: '5px',
            },
          };
        }}
      />

      <IPOInfoModal
        show={showModal}
        onClose={() => setShowModal(false)}
        date={moment(selectedDate).format('YYYY-MM-DD')}
        ipos={ipoData}
      />
    </div>
  );
};

export default CalendarComponent;

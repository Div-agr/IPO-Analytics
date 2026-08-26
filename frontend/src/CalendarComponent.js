import React, { useState, useEffect } from 'react';
import { Calendar, momentLocalizer } from 'react-big-calendar';
import moment from 'moment';
import axios from 'axios';
import IPOInfoModal from './IPOInfoModal';
import 'react-big-calendar/lib/css/react-big-calendar.css';
import './index.css';

const localizer = momentLocalizer(moment);

const CalendarComponent = () => {
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [calendarDate, setCalendarDate] = useState(new Date());
  const [ipoData, setIpoData] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [events, setEvents] = useState([]);

  // Subscriber state
  const [subscriberEmail, setSubscriberEmail] = useState('');
  const [subscribeStatus, setSubscribeStatus] = useState({ message: '', type: '' });
  const [isSubmitting, setIsSubmitting] = useState(false);

  const colors = ['#FF8A65', '#4DB6AC', '#BA68C8', '#7986CB', '#FFD54F', '#81C784', '#F06292'];

  const fetchAllIPOsForMonth = async () => {
    const startOfMonth = '2021-01-01';
    const endOfMonth = moment().endOf('month').format('YYYY-MM-DD');

    try {
      const response = await axios.get(
        `https://ipo-analytics-backend.onrender.com/api/ipo_data_range?start=${startOfMonth}&end=${endOfMonth}`
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

  const handleSubscribe = async (e) => {
    e.preventDefault();
    const cleanedEmail = subscriberEmail.trim();
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    if (!cleanedEmail) {
      setSubscribeStatus({ message: 'Email address is required.', type: 'error' });
      return;
    }

    if (!emailRegex.test(cleanedEmail)) {
      setSubscribeStatus({ message: 'Please enter a valid email address.', type: 'error' });
      return;
    }

    setIsSubmitting(true);
    setSubscribeStatus({ message: '', type: '' });

    try {
      const response = await axios.post('https://ipo-analytics-backend.onrender.com/api/subscribers', {
        email: cleanedEmail,
      });
      setSubscribeStatus({
        message: response.data.message || 'Subscribed successfully!',
        type: 'success',
      });
      setSubscriberEmail('');
    } catch (error) {
      const errorMsg = error.response?.data?.error || 'Subscription failed. Try again.';
      setSubscribeStatus({ message: errorMsg, type: 'error' });
    } finally {
      setIsSubmitting(false);
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

  const handleManualDateChange = (e) => {
    const date = new Date(e.target.value);
    if (!isNaN(date)) {
      setCalendarDate(date);
    }
  };

  useEffect(() => {
    fetchAllIPOsForMonth();
  }, []);

  return (
    <div className="savvycal-calendar-container">
      {/* Page header */}
      <div className="savvycal-header-row">
        <div className="savvycal-title-block">
          <h1 className="savvycal-title">Upcoming IPOs Calendar</h1>
          <input
            type="date"
            className="savvycal-date-input"
            onChange={handleManualDateChange}
            value={moment(calendarDate).format('YYYY-MM-DD')}
          />
        </div>
      </div>

      {/* Subscribe banner - full-width, own row, always above the calendar */}
      <div className="subscribe-banner">
        <div className="subscribe-banner-text">
          <span className="subscribe-banner-icon" aria-hidden="true">🔔</span>
          <div>
            <div className="subscribe-banner-title">Get IPO Alerts</div>
            <div className="subscribe-banner-subtitle">
              Get an email the moment a new IPO opens for applications.
            </div>
          </div>
        </div>

        <form onSubmit={handleSubscribe} className="subscribe-banner-form">
          <input
            type="email"
            placeholder="you@example.com"
            value={subscriberEmail}
            onChange={(e) => setSubscriberEmail(e.target.value)}
            className="subscribe-banner-input"
          />
          <button type="submit" disabled={isSubmitting} className="subscribe-banner-button">
            {isSubmitting ? 'Sending...' : 'Subscribe'}
          </button>
        </form>

        {subscribeStatus.message && (
          <div
            className={`subscribe-banner-status ${subscribeStatus.type === 'success' ? 'is-success' : 'is-error'
              }`}
          >
            {subscribeStatus.message}
          </div>
        )}
      </div>

      {/* Main calendar view */}
      <Calendar
        localizer={localizer}
        events={events}
        startAccessor="start"
        endAccessor="end"
        defaultView="month"
        views={['month']}
        style={{ height: '75vh' }}
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
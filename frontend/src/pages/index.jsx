import Layout from "./Layout.jsx";

import Home from "./Home";

import Courses from "./Courses";

import CourseChat from "./CourseChat";

import CourseContent from "./CourseContent";

import Login from "./Login";
import Signup from "./Signup";

import { BrowserRouter as Router, Route, Routes, useLocation } from 'react-router-dom';
import RequireAuth from "@/components/RequireAuth";

const PAGES = {
    
    Home: Home,
    
    Courses: Courses,
    
    CourseChat: CourseChat,
    
    CourseContent: CourseContent,


    Login: Login,

    Signup: Signup,
    
}

function _getCurrentPage(url) {
    if (url.endsWith('/')) {
        url = url.slice(0, -1);
    }
    let urlLastPart = url.split('/').pop();
    if (urlLastPart.includes('?')) {
        urlLastPart = urlLastPart.split('?')[0];
    }

    const pageName = Object.keys(PAGES).find(page => page.toLowerCase() === urlLastPart.toLowerCase());
    return pageName || Object.keys(PAGES)[0];
}

// Create a wrapper component that uses useLocation inside the Router context
function PagesContent() {
    const location = useLocation();
    const currentPage = _getCurrentPage(location.pathname);
    
    return (
        <Layout currentPageName={currentPage}>
            <Routes>            
                
                    <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
                
                
                <Route path="/Home" element={<RequireAuth><Home /></RequireAuth>} />
                
                <Route path="/Courses" element={<RequireAuth><Courses /></RequireAuth>} />
                
                <Route path="/CourseChat" element={<RequireAuth><CourseChat /></RequireAuth>} />
                
                <Route path="/CourseContent" element={<RequireAuth><CourseContent /></RequireAuth>} />

                <Route path="/login" element={<Login />} />
                <Route path="/signup" element={<Signup />} />
                
            </Routes>
        </Layout>
    );
}

export default function Pages() {
    return (
        <Router>
            <PagesContent />
        </Router>
    );
}
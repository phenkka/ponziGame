// Dynamic Menu JavaScript
document.addEventListener('DOMContentLoaded', function() {
    const dynamicMenuContainer = document.querySelector('.dynamic-menu-container');
    const dynamicMenuTrigger = document.querySelector('.dynamic-menu-trigger');
    const dynamicMenu = document.querySelector('.dynamic-menu');
    const dynamicMenuOverlay = document.querySelector('.dynamic-menu-overlay');
    const menuItems = document.querySelectorAll('.dynamic-menu-item');
    
    if (!dynamicMenuContainer) return;
    
    let isMenuOpen = false;
    let hoverTimeout;
    
    // Function to open menu
    function openMenu() {
        if (isMenuOpen) return;
        
        isMenuOpen = true;
        dynamicMenuContainer.classList.add('menu-open');
        
        // Stop pulsing animation when menu is open
        dynamicMenuTrigger.style.animation = 'none';
        
        // Animate menu items
        menuItems.forEach((item, index) => {
            item.style.animationDelay = `${index * 0.1}s`;
            item.classList.add('animate-in');
        });
    }
    
    // Function to close menu
    function closeMenu() {
        if (!isMenuOpen) return;
        
        isMenuOpen = false;
        dynamicMenuContainer.classList.remove('menu-open');
        
        // Restart pulsing animation when menu is closed
        dynamicMenuTrigger.style.animation = 'menuPulse 3s ease-in-out infinite';
        
        // Remove animation classes
        menuItems.forEach(item => {
            item.classList.remove('animate-in');
        });
    }
    
    // Mouse enter events - only on the trigger (vertical strip)
    dynamicMenuTrigger.addEventListener('mouseenter', function() {
        clearTimeout(hoverTimeout);
        openMenu();
    });
    
    // Mouse leave events - close menu when leaving the menu itself
    dynamicMenu.addEventListener('mouseleave', function(e) {
        // Always close menu when leaving, no matter where cursor goes
        hoverTimeout = setTimeout(() => {
            closeMenu();
        }, 50); // Very quick close
    });
    
    // Click events for menu items
    menuItems.forEach(item => {
        item.addEventListener('click', function(e) {
            // Add click animation
            this.style.transform = 'scale(0.95)';
        setTimeout(() => {
                this.style.transform = '';
        }, 150);
            
            // Close menu after navigation
            setTimeout(() => {
                closeMenu();
            }, 200);
        });
        
        // Add hover effects
        item.addEventListener('mouseenter', function() {
            this.style.transform = 'translateX(10px) scale(1.02)';
        });
        
        item.addEventListener('mouseleave', function() {
            this.style.transform = 'translateX(0) scale(1)';
      });
    });
    
    // Keyboard navigation
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && isMenuOpen) {
            closeMenu();
        }
        
        if (e.key === 'm' || e.key === 'M') {
            if (isMenuOpen) {
                closeMenu();
            } else {
                openMenu();
            }
        }
    });
    
    // Touch events for mobile
    let touchStartX = 0;
    let touchStartY = 0;
    
    dynamicMenuTrigger.addEventListener('touchstart', function(e) {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
    });
    
    dynamicMenuTrigger.addEventListener('touchend', function(e) {
        const touchEndX = e.changedTouches[0].clientX;
        const touchEndY = e.changedTouches[0].clientY;
        const deltaX = touchEndX - touchStartX;
        const deltaY = touchEndY - touchStartY;
        
        // If it's a tap (small movement)
        if (Math.abs(deltaX) < 10 && Math.abs(deltaY) < 10) {
            if (isMenuOpen) {
                closeMenu();
            } else {
                openMenu();
            }
      }
    });
    
    // Close menu when clicking outside
    document.addEventListener('click', function(e) {
        if (isMenuOpen && !dynamicMenu.contains(e.target) && !dynamicMenuTrigger.contains(e.target)) {
            closeMenu();
        }
    });
    
    // Track mouse position and close menu when moving away
    let mouseX = 0;
    let mouseY = 0;
    let lastMouseTime = Date.now();
    
    document.addEventListener('mousemove', function(e) {
        mouseX = e.clientX;
        mouseY = e.clientY;
        lastMouseTime = Date.now();
        
        if (isMenuOpen) {
            const menuRect = dynamicMenu.getBoundingClientRect();
            const triggerRect = dynamicMenuTrigger.getBoundingClientRect();
            
            // Check if mouse is outside menu area
            const isOutsideMenu = mouseX < menuRect.left || mouseX > menuRect.right || 
                                 mouseY < menuRect.top || mouseY > menuRect.bottom;
            
            // Check if mouse is outside trigger area
            const isOutsideTrigger = mouseX < triggerRect.left || mouseX > triggerRect.right || 
                                    mouseY < triggerRect.top || mouseY > triggerRect.bottom;
            
            // Check if mouse is near edges of screen (likely moving to taskbar or browser tabs)
            const isNearRightEdge = mouseX > window.innerWidth - 10;
            const isNearTopEdge = mouseY < 10;
            const isNearBottomEdge = mouseY > window.innerHeight - 10;
            
            if ((isOutsideMenu && isOutsideTrigger) || isNearRightEdge || isNearTopEdge || isNearBottomEdge) {
                clearTimeout(hoverTimeout);
                hoverTimeout = setTimeout(() => {
                    closeMenu();
                }, 50); // Very quick close when near edges
            }
        }
    });
    
    // Check if mouse stopped moving (might be over taskbar or browser UI)
    setInterval(() => {
        if (isMenuOpen && Date.now() - lastMouseTime > 200) {
            // Mouse hasn't moved for 200ms, might be over external UI
            const menuRect = dynamicMenu.getBoundingClientRect();
            const triggerRect = dynamicMenuTrigger.getBoundingClientRect();
            
            const isOverMenu = mouseX >= menuRect.left && mouseX <= menuRect.right && 
                              mouseY >= menuRect.top && mouseY <= menuRect.bottom;
            const isOverTrigger = mouseX >= triggerRect.left && mouseX <= triggerRect.right && 
                                 mouseY >= triggerRect.top && mouseY <= triggerRect.bottom;
            
            if (!isOverMenu && !isOverTrigger) {
                closeMenu();
            }
        }
    }, 100);
    
    // Close menu when mouse leaves the browser window
    document.addEventListener('mouseleave', function() {
        if (isMenuOpen) {
            closeMenu();
        }
    });
    
    // Close menu when mouse enters browser window from outside
    document.addEventListener('mouseenter', function() {
        if (isMenuOpen) {
            // Check if mouse is still over menu when re-entering
            setTimeout(() => {
                const menuRect = dynamicMenu.getBoundingClientRect();
                const triggerRect = dynamicMenuTrigger.getBoundingClientRect();
                
                const isOverMenu = mouseX >= menuRect.left && mouseX <= menuRect.right && 
                                  mouseY >= menuRect.top && mouseY <= menuRect.bottom;
                const isOverTrigger = mouseX >= triggerRect.left && mouseX <= triggerRect.right && 
                                     mouseY >= triggerRect.top && mouseY <= triggerRect.bottom;
                
                if (!isOverMenu && !isOverTrigger) {
                    closeMenu();
                }
            }, 50);
        }
    });
    
    // Add scroll effect to trigger
    let lastScrollY = window.scrollY;
    
    window.addEventListener('scroll', function() {
        const currentScrollY = window.scrollY;
        
        if (currentScrollY > lastScrollY && currentScrollY > 100) {
            // Scrolling down
            dynamicMenuTrigger.style.transform = 'scale(0.8)';
            dynamicMenuTrigger.style.opacity = '0.7';
        } else {
            // Scrolling up
            dynamicMenuTrigger.style.transform = 'scale(1)';
            dynamicMenuTrigger.style.opacity = '1';
        }
        
        lastScrollY = currentScrollY;
    });
    
    // Add visual feedback to trigger
    dynamicMenuTrigger.addEventListener('click', function(e) {
        // Add a subtle animation effect
        this.style.transform = 'scaleX(1.2)';
        setTimeout(() => {
            this.style.transform = 'scaleX(1)';
        }, 150);
    });
});

// Add CSS for additional animations
const additionalStyles = `
    .dynamic-menu-container.menu-open .dynamic-menu {
        transform: translateX(0);
    }
    
    .dynamic-menu-container.menu-open .dynamic-menu-overlay {
        opacity: 1;
        visibility: visible;
    }
    
    .dynamic-menu-item.animate-in {
        animation: slideInLeft 0.3s ease forwards;
    }
    
    .dynamic-menu-trigger {
        transition: all 0.3s ease;
    }
    
    .dynamic-menu-item {
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
`;

// Inject additional styles
const styleSheet = document.createElement('style');
styleSheet.textContent = additionalStyles;
document.head.appendChild(styleSheet);
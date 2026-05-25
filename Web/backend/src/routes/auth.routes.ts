/**
 * Authentication routes
 */
import { Router, Request, Response } from 'express';
import bcrypt from 'bcryptjs';
import { v4 as uuidv4 } from 'uuid';
import { generateToken, authMiddleware, AuthenticatedRequest, JwtPayload } from '../middleware/auth';
import { authRateLimiter } from '../middleware/rateLimiter';
import { asyncHandler } from '../middleware/errorHandler';
import { logger } from '../utils/logger';

const router = Router();

// In-memory user store (replace with database in production)
interface User {
  id: string;
  email: string;
  passwordHash: string;
  role: JwtPayload['role'];
  createdAt: Date;
}

const users = new Map<string, User>();

// Seed admin user on startup
const adminId = uuidv4();
bcrypt.hash('admin123', 10).then(hash => {
  users.set('admin@gridguardian.local', {
    id: adminId,
    email: 'admin@gridguardian.local',
    passwordHash: hash,
    role: 'admin',
    createdAt: new Date(),
  });
  logger.info('Seeded admin user: admin@gridguardian.local');
});

/**
 * POST /auth/register
 * Register a new user
 */
router.post('/register', authRateLimiter, asyncHandler(async (req: Request, res: Response) => {
  const { email, password, role = 'viewer' } = req.body;

  if (!email || !password) {
    res.status(400).json({ error: 'Email and password required' });
    return;
  }

  if (users.has(email)) {
    res.status(409).json({ error: 'Email already registered' });
    return;
  }

  const passwordHash = await bcrypt.hash(password, 10);
  const userId = uuidv4();

  users.set(email, {
    id: userId,
    email,
    passwordHash,
    role: role as JwtPayload['role'],
    createdAt: new Date(),
  });

  const token = generateToken({ userId, email, role });

  logger.info(`User registered: ${email}`);
  res.status(201).json({
    message: 'User registered successfully',
    token,
    user: { id: userId, email, role },
  });
}));

/**
 * POST /auth/login
 * Login and get JWT
 */
router.post('/login', authRateLimiter, asyncHandler(async (req: Request, res: Response) => {
  const { email, password } = req.body;

  if (!email || !password) {
    res.status(400).json({ error: 'Email and password required' });
    return;
  }

  const user = users.get(email);
  if (!user) {
    res.status(401).json({ error: 'Invalid credentials' });
    return;
  }

  const isValid = await bcrypt.compare(password, user.passwordHash);
  if (!isValid) {
    res.status(401).json({ error: 'Invalid credentials' });
    return;
  }

  const token = generateToken({
    userId: user.id,
    email: user.email,
    role: user.role,
  });

  logger.info(`User logged in: ${email}`);
  res.json({
    token,
    user: { id: user.id, email: user.email, role: user.role },
  });
}));

/**
 * POST /auth/refresh
 * Refresh JWT token
 */
router.post('/refresh', authMiddleware, asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  if (!req.user) {
    res.status(401).json({ error: 'Not authenticated' });
    return;
  }

  const token = generateToken(req.user);
  res.json({ token });
}));

/**
 * GET /auth/me
 * Get current user profile
 */
router.get('/me', authMiddleware, asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  if (!req.user) {
    res.status(401).json({ error: 'Not authenticated' });
    return;
  }

  const user = users.get(req.user.email);
  if (!user) {
    res.status(404).json({ error: 'User not found' });
    return;
  }

  res.json({
    id: user.id,
    email: user.email,
    role: user.role,
    createdAt: user.createdAt,
  });
}));

export default router;
